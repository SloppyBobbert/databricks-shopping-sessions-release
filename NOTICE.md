# Provenance and license scope

The [MIT license](LICENSE) applies to the team-owned contributions in this
source snapshot: the implementation, build configuration, test and audit code,
and documentation. “Contributors to the Shopping Sessions implementation”
describes the contributors; it is not the name of an organization.

## Prior work in the native implementation

- `days_from_civil` uses the civil-date formula documented in Howard Hinnant's
  [date algorithms](https://howardhinnant.github.io/date_algorithms.html).
  Hinnant's page states that those formulas are donated to the public domain.
- `mix` uses David Stafford's
  [Mix13 finalizer](https://zimbry.blogspot.com/2011/09/better-bit-mixing-improving-on.html),
  also present in Sebastiano Vigna's
  [SplitMix64 reference](https://prng.di.unimi.it/splitmix64.c), which includes
  a public-domain dedication and a fallback permission grant for that reference
  implementation. The citation identifies the formula and a reference, not
  the implementation's exact historical source or the license of Stafford's
  original article.

## External materials and dependencies

The Buildathon rules, starter distributions, official fixture runner,
comparator and container wrapper, generator, fixture and answer data, and
generated data are not included or covered by this MIT grant. Obtain official
materials separately from the track page. Common build commands and the
challenge's input/output interface are not claimed as original work.

NumPy, PyArrow/Apache Arrow, Polars, the Python and Debian base images, and
the compiler, system libraries and packages they supply are external. Their
own licenses and notices apply. This is a source-only snapshot; it does not
include dependency packages, compiled binaries or a container image.
