import asyncio
import hashlib
from uuid import uuid4

import httpx
import pytest
from news_ai_events import PermanentEventError, TransientEventError
from news_ai_processor.acquisition import (
    ArticleContentAcquisitionTask,
    ArticleContentPolicy,
    ContentOrigin,
    HttpArticleContentAcquirer,
    extract_article_text,
)
from pydantic import ValidationError

SENTINEL = "SUPER_SECRET_ARTICLE_FETCH_TOKEN_123"
POLICY = ArticleContentPolicy(article_min_body_chars=10)
BODY = "The bridge will reopen at 06:30 on 14 September after the structural inspection."


def task(url="https://example.com/story", *, feed_body=None, domain="example.com"):
    return ArticleContentAcquisitionTask(
        event_id=uuid4(),
        article_id=uuid4(),
        discovery_id=uuid4(),
        source_id=uuid4(),
        source_feed_id=uuid4(),
        canonical_url=url,
        source_domain=domain,
        raw_hash="a" * 64,
        snapshot_hash="b" * 64,
        input_json="{}",
        feed_body=feed_body,
    )


async def public(host):
    return ("8.8.8.8",)


def run(handler, work=None, *, resolver=public, policy=POLICY):
    acquirer = HttpArticleContentAcquirer(
        policy, transport=httpx.MockTransport(handler), resolver=resolver
    )
    return asyncio.run(acquirer.acquire(work or task()))


@pytest.mark.parametrize("container", ["article", "main", "body", "div"])
def test_deterministic_extraction_order_entities_boilerplate_and_scripts(container):
    html = (
        f"<html><head><style>hidden</style></head><body><nav>menu</nav><{container}>"
        "<p>First &amp; important paragraph.</p><script>evil()</script><p>Second paragraph.</p>"
        f"</{container}><footer>advertisement</footer></body></html>"
    )
    result = extract_article_text(html, html=True, policy=POLICY)
    assert result == "First & important paragraph.\n\nSecond paragraph."


def test_malformed_html_and_namespace_paragraphs_remain_deterministic():
    assert "First paragraph." in extract_article_text(
        "<article><p>First paragraph.<p>Second paragraph.</article>",
        html=True,
        policy=POLICY,
    )
    assert (
        extract_article_text(
            "<x:div><x:p>First paragraph.</x:p><x:p>Second paragraph.</x:p></x:div>",
            html=True,
            policy=POLICY,
        )
        == "First paragraph.\n\nSecond paragraph."
    )


STORY = BODY + " Published engineering records explain the inspection findings in detail."


@pytest.mark.parametrize(
    "markup,expected",
    [
        pytest.param(
            f"<article>Short teaser.</article><main><p>{STORY}</p></main>",
            STORY,
            id="short-article-then-usable-main",
        ),
        pytest.param(
            "<article>Unrelated first teaser card without full content.</article>"
            "<article>Unrelated second teaser card without full content.</article>"
            "<article>Unrelated third teaser card without full content.</article>"
            f"<main><p>{STORY}</p></main>",
            STORY,
            id="unrelated-article-cards-not-concatenated",
        ),
        pytest.param(
            f"<body><main>{'Unrelated main text. ' * 10}</main>"
            f"<article><p>{STORY}</p></article><p>Unrelated body.</p></body>",
            STORY,
            id="substantial-article-preferred",
        ),
        pytest.param(
            f"<body><article>Teaser.</article><main>Summary.</main><p>{STORY}</p></body>",
            "Teaser.\n\nSummary.\n\n" + STORY,
            id="usable-body-fallback",
        ),
        pytest.param(
            "<body><article>Teaser.</article><main>Summary.</main></body>",
            None,
            id="no-usable-candidate-permanent-error",
        ),
    ],
)
def test_individual_semantic_candidates_skip_teasers_before_body_fallback(markup, expected):
    policy = ArticleContentPolicy(article_min_body_chars=100)
    if expected is None:
        with pytest.raises(PermanentEventError, match="could not be extracted"):
            extract_article_text(markup, html=True, policy=policy)
    else:
        assert extract_article_text(markup, html=True, policy=policy) == expected


@pytest.mark.parametrize("body", ["", "  ", "tiny", "<script>only script</script>"])
def test_unusable_content_is_permanent(body):
    with pytest.raises(PermanentEventError):
        extract_article_text(body, html=True, policy=POLICY)


def test_explicit_feed_body_skips_all_external_calls_and_retains_hash():
    def forbidden(request):
        raise AssertionError("feed body must not cause HTTP")

    result = run(forbidden, task(feed_body=f"<article><p>{BODY}</p></article>", domain=None))
    assert result.body == BODY and result.provenance.origin is ContentOrigin.FEED_CONTENT
    assert result.provenance.body_hash == hashlib.sha256(BODY.encode()).hexdigest()


def test_public_page_pins_ip_preserves_host_and_tls_sni_and_safe_provenance(caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=f"<article><p>{BODY}</p></article>".encode(),
        )

    result = run(handler, task("https://news.example.com/story?token=" + SENTINEL))
    assert result.body == BODY and result.provenance.origin is ContentOrigin.ARTICLE_PAGE
    assert calls[0].url.host == "8.8.8.8"
    assert calls[0].headers["host"] == "news.example.com"
    assert calls[0].extensions["sni_hostname"] == "news.example.com"
    assert "cookie" not in calls[0].headers and "authorization" not in calls[0].headers
    assert result.provenance.final_host == "news.example.com"
    assert SENTINEL not in result.provenance.model_dump_json() + caplog.text


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://node.localhost/",
        "http://node.local/",
        "http://node.internal/",
        "http://169.254.169.254/",
        "http://10.1.2.3/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://100.64.0.1/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://[fd00::1]/",
        "file:///tmp/article",
        "https://user:password@example.com/story",
        "https://example.com.attacker.test/story",
        "https://not-example.com/story",
        "https://example.com:8080/story",
    ],
)
def test_unsafe_urls_never_reach_transport(url):
    calls = []
    with pytest.raises(PermanentEventError):
        run(lambda request: calls.append(request), task(url))
    assert not calls


@pytest.mark.parametrize(
    "addresses",
    [
        (),
        ("127.0.0.1",),
        ("8.8.8.8", "10.0.0.1"),
        ("169.254.169.254",),
        ("224.0.0.1",),
        ("::1",),
        ("fd00::1",),
    ],
)
def test_dns_destinations_are_checked_before_transport(addresses):
    async def resolve(host):
        return addresses

    with pytest.raises(PermanentEventError):
        run(lambda request: pytest.fail("unsafe transport"), resolver=resolve)


@pytest.mark.parametrize(
    "location",
    [
        "http://127.0.0.1/",
        "https://unrelated.com/story",
        "http://169.254.169.254/",
        "https://user:secret@example.com/",
    ],
)
def test_redirect_escapes_are_rejected_before_second_request(location):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": location})

    with pytest.raises(PermanentEventError):
        run(handler)
    assert len(calls) == 1


def test_each_redirect_resolves_again_and_limit_and_loop_are_bounded():
    calls = []

    async def resolve(host):
        calls.append(host)
        return ("8.8.8.8",) if len(calls) == 1 else ("10.0.0.1",)

    with pytest.raises(PermanentEventError):
        run(lambda request: httpx.Response(302, headers={"location": "/second"}), resolver=resolve)
    assert calls == ["example.com", "example.com"]
    for location in ("/story", "/second"):
        with pytest.raises(PermanentEventError):
            run(
                lambda request, location=location: httpx.Response(
                    302, headers={"location": location}
                )
            )


@pytest.mark.parametrize(
    "status,kind",
    [
        (408, TransientEventError),
        (425, TransientEventError),
        (429, TransientEventError),
        (500, TransientEventError),
        (502, TransientEventError),
        (503, TransientEventError),
        (400, PermanentEventError),
        (401, PermanentEventError),
        (403, PermanentEventError),
        (404, PermanentEventError),
        (410, PermanentEventError),
    ],
)
def test_http_failure_classification_is_secret_safe(status, kind):
    with pytest.raises(kind) as caught:
        run(lambda request: httpx.Response(status, content=SENTINEL.encode()))
    assert SENTINEL not in str(caught.value)


@pytest.mark.parametrize(
    "error", [httpx.ConnectError(SENTINEL), httpx.ReadTimeout(SENTINEL), OSError(SENTINEL)]
)
def test_transient_network_errors_are_normalized(error):
    def handler(request):
        raise error

    with pytest.raises(TransientEventError) as caught:
        run(handler)
    assert SENTINEL not in str(caught.value)


@pytest.mark.parametrize(
    "media",
    ["image/png", "video/mp4", "application/pdf", "application/zip", "application/octet-stream"],
)
def test_binary_types_are_permanent(media):
    with pytest.raises(PermanentEventError):
        run(lambda r: httpx.Response(200, headers={"content-type": media}, content=b"binary"))


@pytest.mark.parametrize("encoding", ["utf-8", "iso-8859-1"])
def test_plain_text_declared_charset(encoding):
    body = "Café: the public record contains the full reported reading."
    result = run(
        lambda r: httpx.Response(
            200,
            headers={"content-type": f"text/plain; charset={encoding}"},
            content=body.encode(encoding),
        )
    )
    assert result.body == body


def test_stream_is_bounded_before_materialization_and_closed_on_overflow():
    class Stream(httpx.AsyncByteStream):
        closed = False
        count = 0

        async def __aiter__(self):
            for _ in range(10):
                self.count += 1
                yield b"a" * 700

        async def aclose(self):
            self.closed = True

    stream = Stream()
    with pytest.raises(PermanentEventError):
        run(
            lambda r: httpx.Response(200, headers={"content-type": "text/plain"}, stream=stream),
            policy=ArticleContentPolicy(article_min_body_chars=10, article_max_response_bytes=1024),
        )
    assert stream.closed and stream.count == 2


@pytest.mark.parametrize(
    "values",
    [
        {"article_max_redirects": 11},
        {"article_request_timeout_seconds": 0},
        {"article_max_response_bytes": 99},
        {"article_min_body_chars": 100, "article_max_body_chars": 10},
        {"arbitrary_command": "do not run"},
        {"article_max_redirects": "5"},
        {"article_max_response_bytes": True},
    ],
)
def test_acquisition_configuration_is_bounded_and_closed(values):
    with pytest.raises(ValidationError):
        ArticleContentPolicy.model_validate(values)


def test_missing_domain_fails_closed_before_page_transport():
    with pytest.raises(PermanentEventError):
        run(lambda r: pytest.fail("missing domain must not reach transport"), task(domain=None))


@pytest.mark.parametrize("encoding", ["gzip", "br"])
def test_encoded_response_cannot_bypass_byte_bounds(encoding):
    with pytest.raises(PermanentEventError):
        run(
            lambda r: httpx.Response(
                200,
                headers={
                    "content-type": "text/plain",
                    "content-encoding": encoding,
                },
                stream=httpx.ByteStream(b"not decoded"),
            )
        )


def test_extracted_text_overflow_is_rejected_not_truncated():
    with pytest.raises(PermanentEventError):
        extract_article_text(
            "x" * 21,
            html=False,
            policy=ArticleContentPolicy(
                article_min_body_chars=10,
                article_max_body_chars=20,
            ),
        )


def test_injected_transport_is_caller_owned_and_close_is_idempotent():
    class Transport(httpx.MockTransport):
        closes = 0

        async def aclose(self):
            self.closes += 1

    transport = Transport(lambda r: pytest.fail("must not fetch"))
    acquirer = HttpArticleContentAcquirer(POLICY, transport=transport, resolver=public)

    async def scenario():
        await acquirer.close()
        await acquirer.close()
        assert transport.closes == 0
        with pytest.raises(TransientEventError):
            await acquirer.acquire(task())
        await transport.aclose()
        assert transport.closes == 1

    asyncio.run(scenario())
