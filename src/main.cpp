#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <deque>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <memory>
#include <map>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <vector>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

using SV = std::string_view;
namespace fs = std::filesystem;
constexpr int SHARDS = 32;
constexpr int64_t DAY = 86400000, WEEK = 7 * DAY, GAP = 1800000;
constexpr int64_t BAD = std::numeric_limits<int64_t>::min();
constexpr uint32_t NONE = UINT32_MAX;
int ncpu = 8;

uint64_t mix(uint64_t x) {
    x ^= x >> 30; x *= 0xbf58476d1ce4e5b9ULL;
    x ^= x >> 27; x *= 0x94d049bb133111ebULL;
    return x ^ (x >> 31);
}
uint64_t hashstr(SV s) {
    const char* p = s.data(); size_t n = s.size();
    uint64_t h = n * 0x9e3779b97f4a7c15ULL, x = 0, y = 0;
    if (n <= 16) {
        if (n >= 8) { std::memcpy(&x,p,8); std::memcpy(&y,p+n-8,8); }
        else if (n >= 4) { uint32_t a,b; std::memcpy(&a,p,4); std::memcpy(&b,p+n-4,4); x=a; y=b; }
        else if (n) { x=uint8_t(p[0]); y=uint8_t(p[n/2]) | (uint64_t(uint8_t(p[n-1]))<<8); }
        return mix(h ^ mix(x) ^ y);
    }
    while (n >= 8) { std::memcpy(&x,p,8); h=mix(h^x); p+=8; n-=8; }
    x=0; if(n) std::memcpy(&x,p,n);
    return mix(h^x);
}

// Most event, person, and click IDs fit in 15 bytes. Keeping those bytes with the row avoids a
// random read back into the multi-GB input mapping for every dedup probe.
// Longer IDs retain a borrowed view and still compare every byte.
struct EventId {
    uint64_t a=0,b=0;
    EventId()=default;
    explicit EventId(SV s) {
        if(s.size()<=15) {
            if(s.size()<=8) { if(!s.empty()) std::memcpy(&a,s.data(),s.size()); }
            else { std::memcpy(&a,s.data(),8); std::memcpy(&b,s.data()+8,s.size()-8); }
            b|=uint64_t(s.size())<<56;
        } else { a=reinterpret_cast<uintptr_t>(s.data()); b=uint64_t(s.size())|(1ULL<<63); }
    }
    bool empty() const { return a==0&&b==0; }
    bool large() const { return b>>63; }
    SV view() const {
        return large()?SV(reinterpret_cast<const char*>(uintptr_t(a)),b&~(1ULL<<63))
                      :SV(reinterpret_cast<const char*>(this),b>>56);
    }
    uint64_t hash() const { return large()?hashstr(view()):mix(a^mix(b)); }
    bool operator==(const EventId& other) const {
        if(a==other.a&&b==other.b) return true;
        return large()&&other.large()&&view()==other.view();
    }
    bool operator!=(const EventId& other) const { return !(*this==other); }
};
static_assert(sizeof(EventId)==16,"inline IDs require two contiguous 64-bit words");

// Open addressing stores indices, with complete byte comparisons on collisions.
struct Strings {
    unsigned hash_shift=0;
    bool own_keys=false;
    std::deque<std::string> owned;
    std::vector<SV> keys;
    std::vector<uint32_t> slots;
    void reserve(size_t n) {
        keys.reserve(n);
        size_t cap=16; while(cap < n*10/7+1) cap*=2;
        if(cap > slots.size()) rehash(cap);
    }
    void rehash(size_t cap) {
        std::vector<uint32_t> fresh(cap,0);
        for(uint32_t i=0;i<keys.size();++i) {
            size_t at=(hashstr(keys[i])>>hash_shift)&(cap-1);
            while(fresh[at]) at=(at+1)&(cap-1);
            fresh[at]=i+1;
        }
        slots.swap(fresh);
    }
    uint32_t find(SV key) const {
        if(slots.empty()) return NONE;
        size_t at=(hashstr(key)>>hash_shift)&(slots.size()-1);
        while(slots[at]) {
            uint32_t i=slots[at]-1;
            if(keys[i]==key) return i;
            at=(at+1)&(slots.size()-1);
        }
        return NONE;
    }
    uint32_t add(SV key) {
        if(slots.empty() || (keys.size()+1)*10>slots.size()*7) rehash(slots.empty()?16:slots.size()*2);
        size_t at=(hashstr(key)>>hash_shift)&(slots.size()-1);
        while(slots[at]) {
            uint32_t i=slots[at]-1;
            if(keys[i]==key) return i;
            at=(at+1)&(slots.size()-1);
        }
        if(keys.size()>=NONE) throw std::runtime_error("too many distinct strings");
        if(own_keys) { owned.emplace_back(key); key=owned.back(); }
        uint32_t i=keys.size(); keys.push_back(key); slots[at]=i+1; return i;
    }
};

struct Mapping {
    char* data=nullptr; size_t size=0;
    explicit Mapping(const fs::path& path) {
        int fd=open(path.c_str(),O_RDONLY);
        if(fd<0) throw std::runtime_error("cannot open "+path.string());
        struct stat st{};
        if(fstat(fd,&st)<0) { close(fd); throw std::runtime_error("cannot stat input"); }
        size=st.st_size;
        if(size) {
            void* ptr=mmap(nullptr,size,PROT_READ,MAP_PRIVATE,fd,0);
            if(ptr==MAP_FAILED) { close(fd); throw std::runtime_error("cannot mmap input"); }
            data=static_cast<char*>(ptr);
        }
        close(fd);
    }
    ~Mapping() { if(data) munmap(data,size); }
    Mapping(const Mapping&)=delete;
};
struct Slice { const char* begin; const char* end; };
std::vector<std::unique_ptr<Mapping>> mappings;
std::vector<Slice> load(const fs::path& path, bool directory=false) {
    std::vector<fs::path> paths;
    if(directory) {
        for(const auto& p:fs::directory_iterator(path))
            if(p.is_regular_file() && p.path().extension()==".csv") paths.push_back(p.path());
        std::sort(paths.begin(),paths.end());
    } else paths.push_back(path);
    std::vector<Slice> jobs;
    for(const auto& p:paths) {
        auto m=std::make_unique<Mapping>(p);
        const char* end=m->data ? m->data+m->size : nullptr;
        const char* begin=m->data ? static_cast<const char*>(memchr(m->data,'\n',m->size)) : nullptr;
        if(begin) {
            ++begin;
            while(begin<end) {
                const char* stop=begin+std::min<ptrdiff_t>(end-begin,2*1024*1024);
                if(stop<end) {
                    const char* nl=static_cast<const char*>(memchr(stop,'\n',end-stop));
                    stop=nl?nl+1:end;
                }
                jobs.push_back({begin,stop}); begin=stop;
            }
        }
        mappings.push_back(std::move(m));
    }
    return jobs;
}
template<size_t N> bool csv(SV line, SV (&fields)[N]) {
    const char* p=line.data(); const char* end=p+line.size();
    for(size_t i=0;i<N;++i) {
        const char* comma=static_cast<const char*>(memchr(p,',',end-p));
        if((i+1<N && !comma) || (i+1==N && comma)) return false;
        const char* stop=comma?comma:end;
        SV f(p,stop-p);
        if(!f.empty() && f.front()=='"') {
            if(f.size()<2 || f.back()!='"') return false;
            f.remove_prefix(1); f.remove_suffix(1);
        }
        if(memchr(f.data(),'"',f.size())) return false;
        fields[i]=f;
        p=comma?comma+1:end;
    }
    return true;
}
template<class F> void lines(Slice s,F&& f) {
    const char* p=s.begin;
    while(p<s.end) {
        const char* nl=static_cast<const char*>(memchr(p,'\n',s.end-p));
        const char* stop=nl?nl:s.end;
        f(SV(p,stop-p)); p=nl?nl+1:s.end;
    }
}
template<class F> void parallel(size_t count,F&& f) {
    std::atomic<size_t> next{0}; std::vector<std::thread> threads;
    std::exception_ptr error; std::atomic_flag error_lock=ATOMIC_FLAG_INIT;
    for(int t=0;t<std::min<size_t>(ncpu,count);++t) threads.emplace_back([&,t] {
        try {
            for(size_t i;(i=next.fetch_add(1,std::memory_order_relaxed))<count;) f(i,t);
        } catch(...) {
            while(error_lock.test_and_set(std::memory_order_acquire)) {}
            if(!error) error=std::current_exception();
            error_lock.clear(std::memory_order_release); next.store(count);
        }
    });
    for(auto& t:threads) t.join();
    if(error) std::rethrow_exception(error);
}
int64_t days_from_civil(int y,int m,int d) {
    y-=m<=2; int era=(y>=0?y:y-399)/400;
    unsigned yo=y-era*400, doy=(153*(m+(m>2?-3:9))+2)/5+d-1;
    return int64_t(era)*146097+yo*365+yo/4-yo/100+doy-719468;
}
int64_t timestamp(SV s) {
    if(s.empty()) return BAD;
    if(s.size()!=20 && s.size()!=25) {
        int64_t v=0;
        for(unsigned char c:s) {
            if(c<'0'||c>'9'||v>(INT64_MAX-(c-'0'))/10) return BAD;
            v=v*10+(c-'0');
        }
        return v;
    }
    // A digit-only F3 can also be exactly 20 or 25 characters (leading zeros).
    if(s[4]!='-') {
        int64_t v=0;
        for(unsigned char c:s) {
            if(c<'0'||c>'9'||v>(INT64_MAX-(c-'0'))/10) return BAD;
            v=v*10+(c-'0');
        }
        return v;
    }
    if(s[7]!='-'||s[10]!='T'||s[13]!=':'||s[16]!=':') return BAD;
    constexpr int pos[]={0,1,2,3,5,6,8,9,11,12,14,15,17,18};
    for(int i:pos) if(s[i]<'0'||s[i]>'9') return BAD;
    auto two=[&](int i){return (s[i]-'0')*10+s[i+1]-'0';};
    int y=two(0)*100+two(2),m=two(5),d=two(8),h=two(11),mi=two(14),se=two(17);
    constexpr int mdays[]={0,31,28,31,30,31,30,31,31,30,31,30,31};
    if(m<1||m>12||d<1||d>mdays[m]+(m==2&&y%4==0&&(y%100!=0||y%400==0))||h>23||mi>59||se>59) return BAD;
    int offset=0;
    if(s.size()==20) { if(s[19]!='Z') return BAD; }
    else {
        if((s[19]!='+'&&s[19]!='-')||s[22]!=':') return BAD;
        for(int i:{20,21,23,24}) if(s[i]<'0'||s[i]>'9') return BAD;
        int oh=two(20),om=two(23); if(oh>23||om>59) return BAD;
        offset=(oh*60+om)*(s[19]=='+'?1:-1);
    }
    return (days_from_civil(y,m,d)*86400+h*3600+mi*60+se-offset*60)*1000;
}
uint32_t price(SV s) {
    if(s.empty()) return 0;
    uint32_t v=0;
    for(unsigned char c:s) {
        if(c<'0'||c>'9') return 0;
        if(v>1000000) return 0;
        v=v*10+c-'0';
    }
    return v<=10000000?v:0;
}
int64_t floor_day(int64_t t) { int64_t d=t/DAY; return d-(t%DAY<0); }

struct Identities {
    Strings ids; std::vector<uint32_t> parent,sizes,smallest;
    uint32_t add(SV name) {
        uint32_t id=ids.add(name);
        if(id==parent.size()) { parent.push_back(id); sizes.push_back(1); smallest.push_back(id); }
        return id;
    }
    uint32_t root(uint32_t i) {
        while(parent[i]!=i) { parent[i]=parent[parent[i]]; i=parent[i]; }
        return i;
    }
    void join(SV a,SV b) {
        uint32_t x=add(a), y=add(b); x=root(x); y=root(y);
        if(x==y) return;
        if(sizes[x]<sizes[y]) std::swap(x,y);
        parent[y]=x; sizes[x]+=sizes[y];
        if(ids.keys[smallest[y]]<ids.keys[smallest[x]]) smallest[x]=smallest[y];
    }
    void finish() {
        std::vector<uint32_t> canonical(parent.size());
        for(uint32_t i=0;i<parent.size();++i) canonical[i]=smallest[root(i)];
        parent.swap(canonical);
    }
    SV canonical(SV name) const {
        uint32_t id=ids.find(name); return id==NONE?name:ids.keys[parent[id]];
    }
};
// The ingest winner is retained even when ts == BAD: validation cannot rescue a duplicate.
struct RawEvent { EventId id,user; int64_t ingest,ts; uint32_t price,category; };
struct NamedEvent { EventId user; int64_t ts; uint32_t price,category; };
struct NamedClick { EventId user,id; int64_t ts; uint32_t campaign; };
struct Event { int64_t ts; uint32_t user,price,category; };
struct Click { EventId id; int64_t ts; uint32_t user,campaign; };
struct UserRow { int64_t user,day,sessions,events,revenue,category; };
struct CampaignRow { int64_t campaign,day,sessions,purchasing,revenue,assisted; };
struct CampaignStats {
    int64_t sessions=0,purchasing=0,revenue=0,assisted=0;
    void add(const CampaignStats& other) {
        sessions+=other.sessions; purchasing+=other.purchasing;
        revenue+=other.revenue; assisted+=other.assisted;
    }
};
// The usual short window uses a flat array. Broad windows use only observed
// (day, campaign) pairs, with no arbitrary limit on legal manifest dates.
struct CampaignTotals {
    std::vector<CampaignStats> dense;
    std::map<std::pair<int64_t,uint32_t>,CampaignStats> sparse;
    size_t campaigns=0;
    int64_t first_day=0;
    void init(size_t count,int64_t first,int64_t days) {
        campaigns=count; first_day=first;
        if(uint64_t(days)<=1000000/count) dense.resize(size_t(days)*count);
    }
    CampaignStats& at(int64_t day,uint32_t campaign) {
        if(!dense.empty()) return dense[size_t(day-first_day)*campaigns+campaign];
        return sparse[{day,campaign}];
    }
};

void dictionary(const fs::path& path,const std::vector<SV>& names) {
    std::ofstream out(path,std::ios::binary); out.exceptions(std::ios::failbit|std::ios::badbit);
    uint32_t n=names.size(); out.write(reinterpret_cast<const char*>(&n),4);
    uint64_t total=0;
    for(SV s:names) total+=s.size();
    if(total>INT32_MAX) throw std::runtime_error("dictionary exceeds Arrow string capacity");
    int32_t offset=0; out.write(reinterpret_cast<const char*>(&offset),4);
    for(SV s:names) { offset+=s.size(); out.write(reinterpret_cast<const char*>(&offset),4); }
    for(SV s:names) out.write(s.data(),s.size());
}
template<class T> std::vector<T> concatenate(std::vector<std::vector<std::vector<T>>>& input,int shard) {
    size_t n=0; for(auto& v:input) n+=v[shard].size();
    std::vector<T> out; out.reserve(n);
    for(auto& v:input) {
        out.insert(out.end(),v[shard].begin(),v[shard].end());
        std::vector<T>().swap(v[shard]);
    }
    return out;
}

auto clock_start=std::chrono::steady_clock::now();
void progress(const char* step) {
    std::cerr<<step<<": "<<std::chrono::duration<double>(std::chrono::steady_clock::now()-clock_start).count()<<"s\n";
}

int main(int argc,char** argv) {
 try {
    if(argc!=5) throw std::runtime_error("usage: sessions DATA SCRATCH WINDOW_START WINDOW_END");
    fs::path data=argv[1], scratch=argv[2];
    int64_t start=timestamp(argv[3]),end=timestamp(argv[4]);
    if(start==BAD||end==BAD||end<=start) throw std::runtime_error("invalid manifest window");
    if(const char* n=getenv("NCPU")) ncpu=std::max(1,std::min(64,atoi(n)));
    fs::create_directories(scratch);
    Strings products,categories,campaigns;
    uint32_t unknown=categories.add("unknown");
    std::vector<uint32_t> product_category;
    std::vector<SV> channels;
    for(auto job:load(data/"products.csv")) lines(job,[&](SV line) {
        SV f[2]; if(!csv(line,f)) throw std::runtime_error("bad product lookup");
        uint32_t id=products.add(f[0]);
        if(id>=product_category.size()) product_category.resize(id+1);
        product_category[id]=categories.add(f[1]);
    });
    campaigns.add("organic"); channels.push_back("organic");
    for(auto job:load(data/"campaigns.csv")) lines(job,[&](SV line) {
        SV f[2]; if(!csv(line,f)) throw std::runtime_error("bad campaign lookup");
        uint32_t id=campaigns.add(f[0]);
        if(id>=channels.size()) channels.resize(id+1);
        channels[id]=f[1];
    });
    Identities people;
    auto link_jobs=load(data/"links",true);
    for(auto job:link_jobs) lines(job,[&](SV line) {
        SV f[2]; if(csv(line,f)&&!f[0].empty()&&!f[1].empty()) people.join(f[0],f[1]);
    });
    people.finish(); progress("linked IDs");
    auto event_jobs=load(data/"events",true);
    auto click_jobs=load(data/"clicks",true);
    std::vector<std::vector<std::vector<RawEvent>>> raw(ncpu,std::vector<std::vector<RawEvent>>(SHARDS));
    std::vector<std::vector<std::vector<NamedClick>>> click_parts(ncpu,std::vector<std::vector<NamedClick>>(SHARDS));
    parallel(event_jobs.size(),[&](size_t job,int thread) {
        lines(event_jobs[job],[&](SV line) {
            SV f[7]; if(!csv(line,f)||f[0].empty()) return;
            RawEvent e{EventId(f[0]),{},timestamp(f[3]),BAD,0,0};
            int64_t ts=timestamp(f[2]);
            if(!f[1].empty()&&!f[5].empty()&&ts!=BAD&&ts>=start&&ts<end && e.ingest!=BAD &&
               (e.ingest<=ts || uint64_t(e.ingest)-uint64_t(ts)<=uint64_t(DAY))) {
                bool valid=f[4]=="view"||f[4]=="cart";
                if(f[4]=="purchase") {
                    e.price=price(f[6]); valid=e.price!=0;
                    uint32_t p=products.find(f[5]); e.category=p==NONE?unknown:product_category[p];
                }
                if(valid) { e.ts=ts; e.user=EventId(people.canonical(f[1])); }
            }
            raw[thread][e.id.hash()%SHARDS].push_back(e);
        });
    });
    parallel(click_jobs.size(),[&](size_t job,int thread) {
        lines(click_jobs[job],[&](SV line) {
            SV f[4]; if(!csv(line,f)||f[0].empty()||f[1].empty()) return;
            int64_t ts=timestamp(f[3]); if(ts==BAD||ts<start-WEEK||ts>=end) return;
            uint32_t c=campaigns.find(f[2]); if(c==NONE||c==0) return;
            EventId user(people.canonical(f[1]));
            // Identical copies are harmless in the sweep: credit is unchanged and
            // campaign presence (not click count) determines each assist.
            click_parts[thread][user.hash()%SHARDS].push_back({user,EventId(f[0]),ts,c});
        });
    });
    progress("parsed CSV");
    std::vector<std::vector<std::vector<NamedEvent>>> event_parts(SHARDS,std::vector<std::vector<NamedEvent>>(SHARDS));
    parallel(SHARDS,[&](size_t shard,int) {
        auto events=concatenate(raw,shard);
        size_t cap=16; while(cap<events.size()*10/7+1) cap*=2;
        std::vector<uint32_t> slots(cap,0);
        for(uint32_t i=0;i<events.size();++i) {
            // Low five hash bits already chose this shard. Reusing those bits
            // for the local table would cluster all entries in 1/32 buckets.
            auto& e=events[i]; size_t at=(e.id.hash()>>5)&(cap-1);
            while(slots[at] && events[slots[at]-1].id!=e.id) at=(at+1)&(cap-1);
            if(!slots[at]) slots[at]=i+1;
            else {
                auto& prev=events[slots[at]-1];
                if(e.ingest<prev.ingest) { prev.id={}; slots[at]=i+1; }
                else e.id={};
            }
        }
        for(const auto& e:events) if(!e.id.empty()&&e.ts!=BAD)
            event_parts[shard][e.user.hash()%SHARDS].push_back({e.user,e.ts,e.price,e.category});
    });
    raw.clear(); progress("deduplicated events");
    int64_t first_day=floor_day(start),day_count=floor_day(end-1)-first_day+1;
    size_t campaign_count=campaigns.keys.size();
    std::vector<CampaignTotals> campaign_parts(SHARDS);
    parallel(SHARDS,[&](size_t shard,int) {
        auto named_events=concatenate(event_parts,shard);
        // These names outlive named_events, whose short IDs live inline.
        // Own one copy per person, rather than retaining CSV page references.
        Strings users; users.own_keys=true;
        users.reserve(std::max<size_t>(16,named_events.size()/16));
        std::vector<Event> events; events.reserve(named_events.size());
        for(auto& e:named_events) events.push_back({e.ts,users.add(e.user.view()),e.price,e.category});
        std::vector<NamedEvent>().swap(named_events);
        auto named_clicks=concatenate(click_parts,shard);
        std::vector<Click> clicks; clicks.reserve(named_clicks.size());
        for(auto& c:named_clicks) {
            uint32_t u=users.find(c.user.view());
            if(u!=NONE) clicks.push_back({c.id,c.ts,u,c.campaign});
        }
        std::vector<NamedClick>().swap(named_clicks);
        std::sort(events.begin(),events.end(),[](const Event& a,const Event& b) {
            return a.user!=b.user?a.user<b.user:a.ts<b.ts;
        });
        std::sort(clicks.begin(),clicks.end(),[](const Click& a,const Click& b) {
            if(a.user!=b.user) return a.user<b.user;
            if(a.ts!=b.ts) return a.ts<b.ts;
            return a.id.view()>b.id.view();
        });
        auto& totals=campaign_parts[shard]; totals.init(campaign_count,first_day,day_count);
        std::vector<int64_t> cat_revenue(categories.keys.size(),0);
        std::vector<uint32_t> touched_cats;
        std::vector<uint32_t> counts(campaign_count,0), positions(campaign_count,NONE), active;
        std::ofstream out(scratch/("users-"+std::to_string(shard)+".bin"),std::ios::binary);
        out.exceptions(std::ios::badbit|std::ios::failbit);
        size_t ci=0;
        for(size_t ei=0;ei<events.size();) {
            uint32_t u=events[ei].user;
            size_t ee=ei+1; while(ee<events.size()&&events[ee].user==u) ++ee;
            while(ci<clicks.size()&&clicks[ci].user<u) ++ci;
            size_t ce=ci; while(ce<clicks.size()&&clicks[ce].user==u) ++ce;
            size_t lo=ci,hi=ci;
            UserRow row{int64_t(u),-1,0,0,0,-1};
            auto flush=[&] {
                if(!row.sessions) return;
                int64_t best=0; uint32_t top=NONE;
                for(uint32_t cat:touched_cats) {
                    int64_t rev=cat_revenue[cat];
                    if(rev>best || (rev==best && (top==NONE||categories.keys[cat]<categories.keys[top]))) { best=rev; top=cat; }
                    cat_revenue[cat]=0;
                }
                row.category=top==NONE?-1:int64_t(top);
                out.write(reinterpret_cast<const char*>(&row),sizeof(row)); touched_cats.clear();
            };
            while(ei<ee) {
                int64_t ts=events[ei].ts,day=floor_day(ts);
                if(row.day!=day) { flush(); row={int64_t(u),day,0,0,0,-1}; }
                ++row.sessions;
                int64_t session_revenue=0;
                do {
                    const auto& e=events[ei]; ++row.events;
                    if(e.price) {
                        session_revenue+=e.price;
                        if(cat_revenue[e.category]==0) touched_cats.push_back(e.category);
                        cat_revenue[e.category]+=e.price;
                    }
                    ++ei;
                } while(ei<ee && events[ei].ts-events[ei-1].ts<=GAP);
                row.revenue+=session_revenue;
                while(hi<ce&&clicks[hi].ts<=ts) {
                    uint32_t c=clicks[hi++].campaign;
                    if(counts[c]++==0) { positions[c]=active.size(); active.push_back(c); }
                }
                while(lo<hi&&clicks[lo].ts<ts-WEEK) {
                    uint32_t c=clicks[lo++].campaign;
                    if(--counts[c]==0) {
                        uint32_t p=positions[c],last=active.back(); active[p]=last; positions[last]=p;
                        active.pop_back(); positions[c]=NONE;
                    }
                }
                uint32_t credit=hi>lo?clicks[hi-1].campaign:0;
                auto& credited=totals.at(day,credit); ++credited.sessions;
                credited.purchasing+=session_revenue>0; credited.revenue+=session_revenue;
                for(uint32_t c:active) ++totals.at(day,c).assisted;
            }
            flush();
            for(uint32_t c:active) { counts[c]=0; positions[c]=NONE; }
            active.clear(); ci=ce;
        }
        dictionary(scratch/("users-"+std::to_string(shard)+".dict"),users.keys);
    });
    progress("sessions and attribution");
    std::ofstream out(scratch/"campaigns.bin",std::ios::binary);
    out.exceptions(std::ios::badbit|std::ios::failbit);
    auto write_campaign=[&](int64_t day,uint32_t campaign,const CampaignStats& s) {
        CampaignRow row{campaign,day,s.sessions,s.purchasing,s.revenue,s.assisted};
        if(s.sessions||s.assisted) out.write(reinterpret_cast<const char*>(&row),sizeof(row));
    };
    if(!campaign_parts[0].dense.empty()) {
        for(size_t i=0;i<campaign_parts[0].dense.size();++i) {
            CampaignStats sum;
            for(const auto& part:campaign_parts) sum.add(part.dense[i]);
            write_campaign(first_day+i/campaign_count,i%campaign_count,sum);
        }
    } else {
        std::map<std::pair<int64_t,uint32_t>,CampaignStats> merged;
        for(const auto& part:campaign_parts)
            for(const auto& entry:part.sparse) merged[entry.first].add(entry.second);
        for(const auto& entry:merged) write_campaign(entry.first.first,entry.first.second,entry.second);
    }
    dictionary(scratch/"categories.dict",categories.keys);
    dictionary(scratch/"campaigns.dict",campaigns.keys);
    dictionary(scratch/"channels.dict",channels);
    progress("wrote aggregates");
 } catch(const std::exception& e) { std::cerr<<"pipeline: "<<e.what()<<'\n'; return 1; }
 return 0;
}
