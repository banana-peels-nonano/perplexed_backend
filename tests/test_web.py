"""The frontend is served by the same app (one origin for the session cookie)."""

API = "/api/v1"


async def test_portal_console_and_display_pages_are_served(seeded, make_client):
    client = make_client()
    for path, script in (
        ("/", "app.js"),
        ("/console", "console.js"),
        ("/display", "display.js"),
        ("/register", "register.js"),
    ):
        r = await client.get(path)
        assert r.status_code == 200 and f'src="{script}"' in r.text, path
        csp = r.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    js = await client.get("/app.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert (await client.get("/fonts/press-start-2p-latin-400-normal.woff2")).status_code == 200


async def test_the_api_is_not_shadowed_by_the_static_files(seeded, make_client):
    client = make_client()
    r = await client.get(f"{API}/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
    assert "content-security-policy" not in r.headers  # API responses keep their own headers
    assert (await client.get("/healthz")).json() == {"status": "ok"}


def test_badges_keep_3d_flips_working_in_firefox():
    """mix-blend-mode/filter/isolation inside a badge make Firefox flatten the flip and show
    a face back to front (19 Nov). Keep them out of badge and portrait rules."""
    import re
    from pathlib import Path

    css = Path("web/style.css").read_text()
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if "badge" in selector or "portrait" in selector:
            assert not re.search(r"mix-blend-mode|(?<![-\w])filter\s*:|isolation\s*:", body), (
                selector
            )
