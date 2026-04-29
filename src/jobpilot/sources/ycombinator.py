"""Y Combinator (workatastartup.com) — deferred to v1.1.

The reliable workatastartup.com job feed is behind a login. Until we add
authenticated-browser support, list YC-portfolio companies under
``profile.ats_boards.greenhouse`` (Stripe, Anthropic, OpenAI, Ramp, Linear,
Mercury, etc. are all on Greenhouse) — they'll be picked up by the
greenhouse source with no extra plumbing.

Plan: when v1.1 lands, this file will reuse the gstack ``browse`` skill
to crawl workatastartup.com search results in an authenticated session.
"""
