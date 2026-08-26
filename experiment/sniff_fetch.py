"""
Experiment: sniff fetchReviews for Быстроном 1033677441 to understand 600 cap.
Logs all fetchReviews URLs, query params, and response metadata.
Does NOT modify app/yandex_scraper.py - standalone Playwright script.
"""
import asyncio
import json
import re
from urllib.parse import urlparse, parse_qs

from playwright.async_api import async_playwright

OID = "1033677441"
URL = f"https://yandex.ru/maps/?mode=poi&poi[uri]=ymapsbm1://org?oid={OID}&ll=82.956354,55.032128&poi[point]=82.956354,55.032128&z=13&tab=reviews"

# we will collect
fetches = []

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="ru-RU",
            timezone_id="Asia/Novosibirsk",
        )
        await context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = await context.new_page()

        async def handle_response(resp):
            if "fetchReviews" in resp.url:
                try:
                    url = resp.url
                    parsed = urlparse(url)
                    qs = parse_qs(parsed.query)
                    # try to get json
                    try:
                        j = await resp.json()
                    except:
                        j = {}
                    data = j.get("data", {}) if isinstance(j, dict) else {}
                    reviews = data.get("reviews", []) if isinstance(data, dict) else []
                    total = data.get("total") or data.get("count") or (data.get("pagination") or {}).get("total") or (data.get("params") or {}).get("count")
                    # ranking detection from URL or response
                    ranking = qs.get("ranking", qs.get("sort", ["?"]))[0] if qs.get("ranking") or qs.get("sort") else "N/A"
                    # page detection
                    page_num = qs.get("page", qs.get("pageNumber", ["?"]))[0]
                    print(f"[fetchReviews] page={page_num} ranking={ranking} total={total} got={len(reviews)} url={url[:180]}")
                    fetches.append({"url": url, "qs": qs, "total": total, "got": len(reviews), "page": page_num, "ranking": ranking})
                except Exception as e:
                    print(f"[fetchReviews error] {e} url={resp.url[:150]}")

        page.on("response", lambda r: asyncio.create_task(handle_response(r)))

        print(f"Goto {URL}")
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        # wait for reviews block
        try:
            await page.wait_for_selector("div.business-review-view, [data-testid='review-card']", timeout=10000)
            print("reviews block loaded")
        except:
            print("reviews block NOT loaded")

        # scroll loop: try to trigger many pages
        for i in range(20):
            # scroll container
            await page.evaluate("""() => {
                const els=document.querySelectorAll('div[class*="scroll"]');
                let best=null,maxH=0;
                for(const el of els){ if(el.scrollHeight>el.clientHeight && el.scrollHeight>maxH){maxH=el.scrollHeight; best=el;}}
                if(best) best.scrollTop=best.scrollHeight; else window.scrollTo(0, document.body.scrollHeight);
            }""")
            await page.mouse.wheel(0, 800)
            await page.wait_for_timeout(2500)
            # count fetched so far
            total_got = sum(f["got"] for f in fetches) if fetches else 0
            print(f"--- scroll {i+1}/20 total fetches={len(fetches)} total reviews aggregated={total_got}")
            if len(fetches) >= 15:
                # we have enough to see cap, but continue a bit
                pass

        await page.wait_for_timeout(3000)
        print("\n=== SUMMARY ===")
        for f in fetches:
            print(f"page={f['page']} ranking={f['ranking']} got={f['got']} total={f['total']}")
        # also check last page total
        if fetches:
            max_total = max([f["total"] for f in fetches if f["total"]] or [0])
            print(f"max_total seen={max_total}")
            uniq_pages = len(set(f["page"] for f in fetches))
            print(f"uniq pages={uniq_pages}")
        # try to directly fetch ranking variants via page.evaluate fetch
        print("\n=== TRY direct fetch with different ranking ===")
        # we will try to call fetch via JS fetch inside page context
        for ranking in ["by_relevance_org", "by_time", "by_rating", "by_time_desc", "by_relevance", "recent", "time"]:
            try:
                # construct url from first fetch's base
                if not fetches:
                    break
                base_url = fetches[0]["url"]
                # replace ranking param
                # we need to try to extract businessId
                # parse base
                parsed = urlparse(base_url)
                # try to replace ranking
                # build new url with ranking=ranking and page=13 (beyond 12)
                from urllib.parse import urlencode
                qs = parse_qs(parsed.query)
                # keep businessId etc, change ranking and page
                # flatten
                new_qs = {}
                for k,v in qs.items():
                    new_qs[k] = v[0]
                new_qs["ranking"] = ranking
                new_qs["page"] = "13"
                new_qs["pageSize"] = "50"
                # need to guess host
                test_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(new_qs)}"
                print(f"Trying {ranking} -> {test_url[:180]}")
                result = await page.evaluate("""async (url) => {
                    try {
                        const r = await fetch(url, {credentials: 'include'});
                        const j = await r.json();
                        return {status: r.status, json: j};
                    } catch(e) { return {error: e.toString()}; }
                }""", test_url)
                # print result
                if isinstance(result, dict):
                    if "json" in result:
                        j = result["json"]
                        d = j.get("data", {}) if isinstance(j, dict) else {}
                        revs = d.get("reviews", []) if isinstance(d, dict) else []
                        tot = d.get("total") or d.get("count")
                        print(f"  -> {ranking} page13 status={result.get('status')} got={len(revs) if isinstance(revs,list) else 'N/A'} total={tot} keys={list(d.keys())[:5] if isinstance(d,dict) else 'N/A'}")
                    else:
                        print(f"  -> {ranking} error={result}")
                await page.wait_for_timeout(800)
            except Exception as e:
                print(f"  -> {ranking} exception {e}")

        await browser.close()
        # save
        with open("/tmp/sniff_result.json","w",encoding="utf-8") as f:
            json.dump(fetches,f,ensure_ascii=False,indent=2)
        print("saved /tmp/sniff_result.json")

if __name__ == "__main__":
    asyncio.run(main())
