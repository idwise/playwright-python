# Copyright (c) Microsoft Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import asyncio
import json

import pytest

from playwright.helper import Error
from playwright.page import Page


async def test_page_route_should_intercept(page, server):
    intercepted = []

    async def handle_request(route, request, intercepted):
        assert route.request == request
        assert "empty.html" in request.url
        assert request.headers["user-agent"]
        assert request.method == "GET"
        assert request.postData is None
        assert request.isNavigationRequest
        assert request.resourceType == "document"
        assert request.frame == page.mainFrame
        assert request.frame.url == "about:blank"
        await route.continue_()
        intercepted.append(True)

    await page.route(
        "**/empty.html",
        lambda route, request: asyncio.create_task(
            handle_request(route, request, intercepted)
        ),
    )

    response = await page.goto(server.EMPTY_PAGE)
    assert response.ok
    assert len(intercepted) == 1


async def test_page_route_should_unroute(page: Page, server):
    intercepted = []

    def handler1(route, request):
        intercepted.append(1)
        asyncio.create_task(route.continue_())

    await page.route("**/empty.html", handler1)
    await page.route(
        "**/empty.html",
        lambda route, _: (
            intercepted.append(2),  # type: ignore
            asyncio.create_task(route.continue_()),
        ),
    )

    await page.route(
        "**/empty.html",
        lambda route, _: (
            intercepted.append(3),  # type: ignore
            asyncio.create_task(route.continue_()),
        ),
    )

    await page.route(
        "**/*",
        lambda route, _: (
            intercepted.append(4),  # type: ignore
            asyncio.create_task(route.continue_()),
        ),
    )

    await page.goto(server.EMPTY_PAGE)
    assert intercepted == [1]

    intercepted = []
    await page.unroute("**/empty.html", handler1)
    await page.goto(server.EMPTY_PAGE)
    assert intercepted == [2]

    intercepted = []
    await page.unroute("**/empty.html")
    await page.goto(server.EMPTY_PAGE)
    assert intercepted == [4]


async def test_page_route_should_work_when_POST_is_redirected_with_302(page, server):
    server.set_redirect("/rredirect", "/empty.html")
    await page.goto(server.EMPTY_PAGE)
    await page.route("**/*", lambda route, _: asyncio.create_task(route.continue_()))
    await page.setContent(
        """
      <form action='/rredirect' method='post'>
        <input type="hidden" id="foo" name="foo" value="FOOBAR">
      </form>
    """
    )
    await asyncio.gather(
        page.evalOnSelector("form", "form => form.submit()"), page.waitForNavigation()
    )


# @see https://github.com/GoogleChrome/puppeteer/issues/3973
async def test_page_route_should_work_when_header_manipulation_headers_with_redirect(
    page, server
):
    server.set_redirect("/rrredirect", "/empty.html")
    await page.route(
        "**/*",
        lambda route, _: asyncio.create_task(
            route.continue_(headers={**route.request.headers, "foo": "bar"})
        ),
    )

    await page.goto(server.PREFIX + "/rrredirect")


# @see https://github.com/GoogleChrome/puppeteer/issues/4743
async def test_page_route_should_be_able_to_remove_headers(page, server):
    async def handle_request(route):
        headers = route.request.headers
        if "origin" in headers:
            del headers["origin"]
        await route.continue_(headers=headers)

    await page.route(
        "**/*",  # remove "origin" header
        lambda route, _: asyncio.create_task(handle_request(route)),
    )

    [serverRequest, _] = await asyncio.gather(
        server.wait_for_request("/empty.html"), page.goto(server.PREFIX + "/empty.html")
    )
    assert serverRequest.getHeader("origin") is None


async def test_page_route_should_contain_referer_header(page, server):
    requests = []
    await page.route(
        "**/*",
        lambda route, _: (
            requests.append(route.request),
            asyncio.create_task(route.continue_()),
        ),
    )

    await page.goto(server.PREFIX + "/one-style.html")
    assert "/one-style.css" in requests[1].url
    assert "/one-style.html" in requests[1].headers["referer"]


async def test_page_route_should_properly_return_navigation_response_when_URL_has_cookies(
    context, page, server
):
    # Setup cookie.
    await page.goto(server.EMPTY_PAGE)
    await context.addCookies(
        [{"url": server.EMPTY_PAGE, "name": "foo", "value": "bar"}]
    )

    # Setup request interception.
    await page.route("**/*", lambda route, _: asyncio.create_task(route.continue_()))
    response = await page.reload()
    assert response.status == 200


async def test_page_route_should_show_custom_HTTP_headers(page, server):
    await page.setExtraHTTPHeaders({"foo": "bar"})

    def assert_headers(request):
        assert request.headers["foo"] == "bar"

    await page.route(
        "**/*",
        lambda route, _: (
            assert_headers(route.request),
            asyncio.create_task(route.continue_()),
        ),
    )

    response = await page.goto(server.EMPTY_PAGE)
    assert response.ok


# @see https://github.com/GoogleChrome/puppeteer/issues/4337
async def test_page_route_should_work_with_redirect_inside_sync_XHR(page, server):
    await page.goto(server.EMPTY_PAGE)
    server.set_redirect("/logo.png", "/pptr.png")
    await page.route("**/*", lambda route, _: asyncio.create_task(route.continue_()))
    status = await page.evaluate(
        """async() => {
      const request = new XMLHttpRequest();
      request.open('GET', '/logo.png', false);  // `false` makes the request synchronous
      request.send(null);
      return request.status;
    }"""
    )

    assert status == 200


async def test_page_route_should_work_with_custom_referer_headers(page, server):
    await page.setExtraHTTPHeaders({"referer": server.EMPTY_PAGE})

    def assert_headers(route):
        assert route.request.headers["referer"] == server.EMPTY_PAGE

    await page.route(
        "**/*",
        lambda route, _: (
            assert_headers(route),
            asyncio.create_task(route.continue_()),
        ),
    )

    response = await page.goto(server.EMPTY_PAGE)
    assert response.ok


async def test_page_route_should_be_abortable(page, server):
    await page.route(r"/\.css$/", lambda route, _: asyncio.create_task(route.abort()))
    failed = []

    def handle_request(request):
        if request.url.includes(".css"):
            failed.append(True)

    page.on("requestfailed", handle_request)

    response = await page.goto(server.PREFIX + "/one-style.html")
    assert response.ok
    assert response.request.failure is None
    assert len(failed) == 0


async def test_page_route_should_be_abortable_with_custom_error_codes(
    page: Page, server, is_webkit, is_firefox
):
    await page.route(
        "**/*",
        lambda route, _: asyncio.create_task(route.abort("internetdisconnected")),
    )
    failed_requests = []
    page.on("requestfailed", lambda request: failed_requests.append(request))
    with pytest.raises(Error):
        await page.goto(server.EMPTY_PAGE)
    assert len(failed_requests) == 1
    failed_request = failed_requests[0]
    if is_webkit:
        assert failed_request.failure == "Request intercepted"
    elif is_firefox:
        assert failed_request.failure == "NS_ERROR_OFFLINE"
    else:
        assert failed_request.failure == "net::ERR_INTERNET_DISCONNECTED"


async def test_page_route_should_send_referer(page, server):
    await page.setExtraHTTPHeaders({"referer": "http://google.com/"})

    await page.route("**/*", lambda route, _: asyncio.create_task(route.continue_()))
    [request, _] = await asyncio.gather(
        server.wait_for_request("/grid.html"), page.goto(server.PREFIX + "/grid.html"),
    )
    assert request.getHeader("referer") == "http://google.com/"


async def test_page_route_should_fail_navigation_when_aborting_main_resource(
    page, server, is_webkit, is_firefox
):
    await page.route("**/*", lambda route, _: asyncio.create_task(route.abort()))
    with pytest.raises(Error) as exc:
        await page.goto(server.EMPTY_PAGE)
    assert exc
    if is_webkit:
        assert "Request intercepted" in exc.value.message
    elif is_firefox:
        assert "NS_ERROR_FAILURE" in exc.value.message
    else:
        assert "net::ERR_FAILED" in exc.value.message


async def test_page_route_should_not_work_with_redirects(page, server):
    intercepted = []
    await page.route(
        "**/*",
        lambda route, _: (
            asyncio.create_task(route.continue_()),
            intercepted.append(route.request),
        ),
    )

    server.set_redirect("/non-existing-page.html", "/non-existing-page-2.html")
    server.set_redirect("/non-existing-page-2.html", "/non-existing-page-3.html")
    server.set_redirect("/non-existing-page-3.html", "/non-existing-page-4.html")
    server.set_redirect("/non-existing-page-4.html", "/empty.html")

    response = await page.goto(server.PREFIX + "/non-existing-page.html")
    assert response.status == 200
    assert "empty.html" in response.url

    assert len(intercepted) == 1
    assert intercepted[0].resourceType == "document"
    assert intercepted[0].isNavigationRequest
    assert "/non-existing-page.html" in intercepted[0].url

    chain = []
    r = response.request
    while r:
        chain.append(r)
        assert r.isNavigationRequest
        r = r.redirectedFrom

    assert len(chain) == 5
    assert "/empty.html" in chain[0].url
    assert "/non-existing-page-4.html" in chain[1].url
    assert "/non-existing-page-3.html" in chain[2].url
    assert "/non-existing-page-2.html" in chain[3].url
    assert "/non-existing-page.html" in chain[4].url
    for idx, _ in enumerate(chain):
        assert chain[idx].redirectedTo == (chain[idx - 1] if idx > 0 else None)


async def test_page_route_should_work_with_redirects_for_subresources(page, server):
    intercepted = []
    await page.route(
        "**/*",
        lambda route, _: (
            asyncio.create_task(route.continue_()),
            intercepted.append(route.request),
        ),
    )

    server.set_redirect("/one-style.css", "/two-style.css")
    server.set_redirect("/two-style.css", "/three-style.css")
    server.set_redirect("/three-style.css", "/four-style.css")
    server.set_route(
        "/four-style.css",
        lambda req: (req.write(b"body {box-sizing: border-box; }"), req.finish()),
    )

    response = await page.goto(server.PREFIX + "/one-style.html")
    assert response.status == 200
    assert "one-style.html" in response.url

    assert len(intercepted) == 2
    assert intercepted[0].resourceType == "document"
    assert "one-style.html" in intercepted[0].url

    r = intercepted[1]
    for url in [
        "/one-style.css",
        "/two-style.css",
        "/three-style.css",
        "/four-style.css",
    ]:
        assert r.resourceType == "stylesheet"
        assert url in r.url
        r = r.redirectedTo
    assert r is None


async def test_page_route_should_work_with_equal_requests(page, server):
    await page.goto(server.EMPTY_PAGE)
    hits = [True]

    def handle_request(request, hits):
        request.write(str(len(hits) * 11).encode())
        request.finish()
        hits.append(True)

    server.set_route("/zzz", lambda r: handle_request(r, hits))

    spinner = []

    async def handle_route(route, spinner):
        if len(spinner) == 1:
            await route.abort()
            spinner.pop(0)
        else:
            await route.continue_()
            spinner.append(True)

    # Cancel 2nd request.
    await page.route("**/*", lambda r, _: asyncio.create_task(handle_route(r, spinner)))

    results = []
    for idx in range(3):
        results.append(
            await page.evaluate(
                """() => fetch('/zzz').then(response => response.text()).catch(e => 'FAILED')"""
            )
        )
    assert results == ["11", "FAILED", "22"]


async def test_page_route_should_navigate_to_dataURL_and_not_fire_dataURL_requests(
    page, server
):
    requests = []
    await page.route(
        "**/*",
        lambda route, _: (
            requests.append(route.request),
            asyncio.create_task(route.continue_()),
        ),
    )

    data_URL = "data:text/html,<div>yo</div>"
    response = await page.goto(data_URL)
    assert response is None
    assert len(requests) == 0


async def test_page_route_should_be_able_to_fetch_dataURL_and_not_fire_dataURL_requests(
    page, server
):
    await page.goto(server.EMPTY_PAGE)
    requests = []
    await page.route(
        "**/*",
        lambda route, _: (
            requests.append(route.request),
            asyncio.create_task(route.continue_()),
        ),
    )

    data_URL = "data:text/html,<div>yo</div>"
    text = await page.evaluate("url => fetch(url).then(r => r.text())", data_URL)
    assert text == "<div>yo</div>"
    assert len(requests) == 0


async def test_page_route_should_navigate_to_URL_with_hash_and_and_fire_requests_without_hash(
    page, server
):
    requests = []
    await page.route(
        "**/*",
        lambda route, _: (
            requests.append(route.request),
            asyncio.create_task(route.continue_()),
        ),
    )

    response = await page.goto(server.EMPTY_PAGE + "#hash")
    assert response.status == 200
    assert response.url == server.EMPTY_PAGE
    assert len(requests) == 1
    assert requests[0].url == server.EMPTY_PAGE


async def test_page_route_should_work_with_encoded_server(page, server):
    # The requestWillBeSent will report encoded URL, whereas interception will
    # report URL as-is. @see crbug.com/759388
    await page.route("**/*", lambda route, _: asyncio.create_task(route.continue_()))
    response = await page.goto(server.PREFIX + "/some nonexisting page")
    assert response.status == 404


async def test_page_route_should_work_with_badly_encoded_server(page, server):
    server.set_route("/malformed?rnd=%911", lambda req: req.finish())
    await page.route("**/*", lambda route, _: asyncio.create_task(route.continue_()))
    response = await page.goto(server.PREFIX + "/malformed?rnd=%911")
    assert response.status == 200


async def test_page_route_should_work_with_encoded_server___2(page, server):
    # The requestWillBeSent will report URL as-is, whereas interception will
    # report encoded URL for stylesheet. @see crbug.com/759388
    requests = []
    await page.route(
        "**/*",
        lambda route, _: (
            asyncio.create_task(route.continue_()),
            requests.append(route.request),
        ),
    )

    response = await page.goto(
        f"""data:text/html,<link rel="stylesheet" href="{server.PREFIX}/fonts?helvetica|arial"/>"""
    )
    assert response is None
    assert len(requests) == 1
    assert (await requests[0].response()).status == 404


async def test_page_route_should_not_throw_Invalid_Interception_Id_if_the_request_was_cancelled(
    page, server
):
    await page.setContent("<iframe></iframe>")
    route_future = asyncio.Future()
    await page.route("**/*", lambda r, _: route_future.set_result(r))

    await asyncio.gather(
        page.waitForEvent("request"),  # Wait for request interception.
        page.evalOnSelector(
            "iframe", """(frame, url) => frame.src = url""", server.EMPTY_PAGE
        ),
    )
    # Delete frame to cause request to be canceled.
    await page.evalOnSelector("iframe", "frame => frame.remove()")
    route = await route_future
    await route.continue_()


async def test_page_route_should_intercept_main_resource_during_cross_process_navigation(
    page, server
):
    await page.goto(server.EMPTY_PAGE)
    intercepted = []
    await page.route(
        server.CROSS_PROCESS_PREFIX + "/empty.html",
        lambda route, _: (
            intercepted.append(True),
            asyncio.create_task(route.continue_()),
        ),
    )

    response = await page.goto(server.CROSS_PROCESS_PREFIX + "/empty.html")
    assert response.ok
    assert len(intercepted) == 1


async def test_page_route_should_create_a_redirect(page, server):
    await page.goto(server.PREFIX + "/empty.html")

    async def handle_route(route, request):
        if request.url != (server.PREFIX + "/redirect_this"):
            return await route.continue_()
        await route.fulfill(status=301, headers={"location": "/empty.html"})

    await page.route(
        "**/*",
        lambda route, request: asyncio.create_task(handle_route(route, request)),
    )

    text = await page.evaluate(
        """async url => {
      const data = await fetch(url);
      return data.text();
    }""",
        server.PREFIX + "/redirect_this",
    )
    assert text == ""


async def test_page_route_should_support_cors_with_GET(page, server):
    await page.goto(server.EMPTY_PAGE)

    async def handle_route(route, request):
        headers = (
            {"access-control-allow-origin": "*"}
            if request.url.endswith("allow")
            else {}
        )
        await route.fulfill(
            contentType="application/json",
            headers=headers,
            status=200,
            body=json.dumps(["electric", "gas"]),
        )

    await page.route(
        "**/cars*",
        lambda route, request: asyncio.create_task(handle_route(route, request)),
    )
    # Should succeed
    resp = await page.evaluate(
        """async () => {
        const response = await fetch('https://example.com/cars?allow', { mode: 'cors' });
        return response.json();
      }"""
    )

    assert resp == ["electric", "gas"]

    # Should be rejected
    with pytest.raises(Error) as exc:
        await page.evaluate(
            """async () => {
            const response = await fetch('https://example.com/cars?reject', { mode: 'cors' });
            return response.json();
        }"""
        )
    assert "failed" in exc.value.message


async def test_page_route_should_support_cors_with_POST(page, server):
    await page.goto(server.EMPTY_PAGE)
    await page.route(
        "**/cars",
        lambda route, _: asyncio.create_task(
            route.fulfill(
                contentType="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
                status=200,
                body=json.dumps(["electric", "gas"]),
            )
        ),
    )

    resp = await page.evaluate(
        """async () => {
      const response = await fetch('https://example.com/cars', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        mode: 'cors',
        body: JSON.stringify({ 'number': 1 })
      });
      return response.json();
    }"""
    )

    assert resp == ["electric", "gas"]


async def test_page_route_should_support_cors_for_different_methods(page, server):
    await page.goto(server.EMPTY_PAGE)
    await page.route(
        "**/cars",
        lambda route, request: asyncio.create_task(
            route.fulfill(
                contentType="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
                status=200,
                body=json.dumps([request.method, "electric", "gas"]),
            )
        ),
    )

    # First POST
    resp = await page.evaluate(
        """async () => {
        const response = await fetch('https://example.com/cars', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          mode: 'cors',
          body: JSON.stringify({ 'number': 1 })
        });
        return response.json();
      }"""
    )

    assert resp == ["POST", "electric", "gas"]
    # Then DELETE
    resp = await page.evaluate(
        """async () => {
        const response = await fetch('https://example.com/cars', {
          method: 'DELETE',
          headers: {},
          mode: 'cors',
          body: ''
        });
        return response.json();
      }"""
    )

    assert resp == ["DELETE", "electric", "gas"]


async def test_request_continue_should_work(page, server):
    await page.route("**/*", lambda route, _: asyncio.create_task(route.continue_()))
    await page.goto(server.EMPTY_PAGE)


async def test_request_continue_should_amend_HTTP_headers(page, server):
    await page.route(
        "**/*",
        lambda route, _: asyncio.create_task(
            route.continue_(headers={**route.request.headers, "FOO": "bar"})
        ),
    )

    await page.goto(server.EMPTY_PAGE)
    [request, _] = await asyncio.gather(
        server.wait_for_request("/sleep.zzz"),
        page.evaluate('() => fetch("/sleep.zzz")'),
    )
    assert request.getHeader("foo") == "bar"


async def test_request_continue_should_amend_method(page, server):
    server_request = asyncio.create_task(server.wait_for_request("/sleep.zzz"))
    await page.goto(server.EMPTY_PAGE)
    await page.route(
        "**/*", lambda route, _: asyncio.create_task(route.continue_(method="POST"))
    )
    [request, _] = await asyncio.gather(
        server.wait_for_request("/sleep.zzz"),
        page.evaluate('() => fetch("/sleep.zzz")'),
    )
    assert request.method.decode() == "POST"
    assert (await server_request).method.decode() == "POST"


async def test_request_continue_should_amend_method_on_main_request(page, server):
    request = asyncio.create_task(server.wait_for_request("/empty.html"))
    await page.route(
        "**/*", lambda route, _: asyncio.create_task(route.continue_(method="POST"))
    )
    await page.goto(server.EMPTY_PAGE)
    assert (await request).method.decode() == "POST"


async def test_request_continue_should_amend_post_data(page, server):
    await page.goto(server.EMPTY_PAGE)
    await page.route(
        "**/*",
        lambda route, _: asyncio.create_task(route.continue_(postData=b"doggo")),
    )

    [serverRequest, _] = await asyncio.gather(
        server.wait_for_request("/sleep.zzz"),
        page.evaluate(
            """
        () => fetch('/sleep.zzz', { method: 'POST', body: 'birdy' })
      """
        ),
    )
    assert serverRequest.post_body == "doggo"


async def test_request_fulfill_should_work_a(page, server):
    await page.route(
        "**/*",
        lambda route, _: asyncio.create_task(
            route.fulfill(
                status=201,
                headers={"foo": "bar"},
                contentType="text/html",
                body="Yo, page!",
            )
        ),
    )

    response = await page.goto(server.EMPTY_PAGE)
    assert response.status == 201
    assert response.headers["foo"] == "bar"
    assert await page.evaluate("() => document.body.textContent") == "Yo, page!"


async def test_request_fulfill_should_work_with_status_code_422(page, server):
    await page.route(
        "**/*",
        lambda route, _: asyncio.create_task(
            route.fulfill(status=422, body="Yo, page!")
        ),
    )

    response = await page.goto(server.EMPTY_PAGE)
    assert response.status == 422
    assert response.statusText == "Unprocessable Entity"
    assert await page.evaluate("() => document.body.textContent") == "Yo, page!"