# seo-domain-snapshot

The first question of every SEO audit, in one call: how much organic traffic a domain gets, how
many keywords it ranks for and where, whether it buys ads, and how big its backlink profile is.

Two steps run in parallel: SE Ranking's domain overview (traffic, keyword counts by position band,
new and lost keywords, paid keywords) and treg's routed backlinks summary (the cheapest provider
that answers; `backlinks_source` names it). A JSON-steps recipe: no logic, just two tools joined.

`source` picks the Google database (`us` by default). Priced `percent`: the maker earns 20% of the
provider fees, so a run costs about $0.037 (about $0.031 in fees plus about $0.006).

Why DataForSEO is not a step here: its live endpoints take a JSON array body, and a JSON step's
`input` is an object. A script recipe can call it (`ctx.call(id, {body: [...]})`).
