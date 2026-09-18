"""Scraper regression tests.

Unlike parser.py/normalizer.py's tests (which run entirely offline
against saved fixture HTML), scraper.py's whole job is talking to the
live MediaWiki API, so these need network access.

Run with: ./venv/bin/python3 -m pytest test_scraper.py -v
(or plain: ./venv/bin/python3 test_scraper.py)
"""

import scraper


def test_redirect_is_followed_to_the_real_page():
    # "Piccolo" is a redirect to "Piccolo (Dragon Ball Z)" on the wiki.
    # Without redirects=1 in the API request, action=parse returns only
    # a "Redirect to: ..." stub with no Powers and Stats section at all
    # - found while diagnosing why Piccolo had zero parsed stats (it
    # wasn't a parser bug; the scraper just never followed the redirect,
    # so the parser was correctly finding nothing to parse).
    html = scraper.fetch_page("Piccolo", force_refresh=True)
    assert "Redirect to" not in html
    assert "Attack Potency" in html
    assert "Powers_and_Stats" in html or "Power_and_Stats" in html


if __name__ == "__main__":
    import sys

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
