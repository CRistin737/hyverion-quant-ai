from __future__ import annotations

import respx
from httpx import Response

from trading_bot.data.intelligence import RedditOfficialConnector, XOfficialConnector


@respx.mock
async def test_x_official_connector_normalizes_recent_posts() -> None:
    respx.get("https://api.x.com/2/tweets/search/recent").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": "1",
                        "text": "BTC breakout",
                        "author_id": "u1",
                        "created_at": "2026-09-14T12:00:00Z",
                        "public_metrics": {"like_count": 10, "reply_count": 2},
                    }
                ],
                "includes": {"users": [{"id": "u1", "username": "analyst"}]},
            },
        )
    )
    connector = XOfficialConnector("bearer")
    posts = await connector.fetch_recent(query="BTC", asset="QQQ", max_results=10)
    assert posts[0].source == "x-official"
    assert posts[0].author_id == "analyst"
    assert posts[0].engagement == 12


@respx.mock
async def test_reddit_official_connector_uses_oauth_and_normalizes_listing() -> None:
    respx.post("https://www.reddit.com/api/v1/access_token").mock(
        return_value=Response(200, json={"access_token": "token", "expires_in": 3600})
    )
    respx.get("https://oauth.reddit.com/search").mock(
        return_value=Response(
            200,
            json={
                "data": {
                    "children": [
                        {
                            "data": {
                                "id": "abc",
                                "title": "BTC market update",
                                "selftext": "Public analysis",
                                "author": "user",
                                "created_utc": 1790000000,
                            }
                        }
                    ]
                }
            },
        )
    )
    connector = RedditOfficialConnector("client", "secret")
    posts = await connector.fetch_search(query="BTC", asset="QQQ", limit=1)
    assert posts[0].source == "reddit-official"
    assert posts[0].text.startswith("BTC market update")
