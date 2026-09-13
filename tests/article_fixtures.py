"""Offline official acquisition boundary for tests unrelated to HTTP policy."""

import httpx
from news_ai_processor.acquisition import ArticleContentPolicy, HttpArticleContentAcquirer


def offline_acquirer(
    body="Full publisher article material reviewed through deterministic extraction.",
):
    async def resolve(host):
        return ("8.8.8.8",)

    return HttpArticleContentAcquirer(
        ArticleContentPolicy(article_min_body_chars=1),
        resolver=resolve,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=f"<article><p>{body}</p></article>".encode(),
            )
        ),
    )
