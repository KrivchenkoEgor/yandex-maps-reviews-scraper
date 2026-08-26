"""
Sniff ALL business-related endpoints, not just fetchReviews.
Goal: find alternative endpoint that might return >600 reviews.
"""
import asyncio
import json
import re
from urllib.parse import urlparse

from playwright.async_api import async_playwright

OID = "1033677441"
URL = f"https://yandex.ru/maps/?mode=poi&poi[uri]=ymapsbm1://org?oid={OID}&ll=82.956354,55.032128&poi[point]=82.956354,55.032128&z=13&tab=reviews"

# keywords to flag
KEYWORDS = ["business", "review", "rating", "comment", "org", "maps/api"]

captured = []

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled","--no-sandbox"])
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
            viewport={"width":1280,"height":800},
            locale="ru-RU",
            timezone_id="Asia/Novosibirsk"
        )
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = await ctx.new_page()

        async def on_resp(resp):
            url = resp.url
            # capture any api/maps/business/ review-like
            if any(k in url.lower() for k in KEYWORDS):
                try:
                    # try json
                    try:
                        j = await resp.json()
                    except:
                        j = None
                    # summarize
                    info = {
                        "url": url[:400],
                        "status": resp.status,
                        "has_json": j is not None,
                    }
                    if isinstance(j, dict):
                        data = j.get("data") or j
                        if isinstance(data, dict):
                            # try to count reviews-like arrays
                            for k,v in data.items():
                                if isinstance(v, list) and len(v)>0 and isinstance(v[0], dict):
                                    # check if looks like review
                                    sample = v[0]
                                    if "text" in sample or "rating" in sample or "author" in sample:
                                        info[f"list_{k}_len"] = len(v)
                                        if k=="reviews":
                                            info["reviews_sample_id"] = sample.get("reviewId") or sample.get("id") or str(sample)[:80]
                            # check total-like fields
                            for tk in ["total","count","totalReviews","reviewCount","pagination","params","totalPages"]:
                                if tk in data:
                                    info[tk]= str(data[tk])[:200]
                            # if data itself is list of reviews
                    captured.append(info)
                    # print interesting
                    if "fetchReviews" in url or "review" in url.lower():
                        print(f"[CAP] {info}")
                except Exception as e:
                    print(f"cap err {e}")

        page.on("response", lambda r: asyncio.create_task(on_resp(r)))

        print(f"Goto {URL}")
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)
        try:
            await page.wait_for_selector("div.business-review-view", timeout=8000)
            print("reviews block loaded")
        except:
            print("no reviews block")
        # scroll to trigger
        for i in range(12):
            await page.evaluate("""()=>{
                const els=document.querySelectorAll('div[class*="scroll"]');
                let best=null,maxH=0; for(const el of els){ if(el.scrollHeight>el.clientHeight && el.scrollHeight>maxH){maxH=el.scrollHeight; best=el;}}
                if(best) best.scrollTop=best.scrollHeight; else window.scrollTo(0, document.body.scrollHeight);
            }""")
            await page.mouse.wheel(0, 800)
            await page.wait_for_timeout(2500)
            print(f"scroll {i+1}/12 captured={len(captured)}")
        await page.wait_for_timeout(3000)
        # dump all captured
        print("\n=== ALL CAPTURED (filtered) ===")
        for c in captured:
            print(json.dumps(c, ensure_ascii=False))
        # unique urls by base path
        uniq_paths = {}
        for c in captured:
            path = urlparse(c["url"]).path
            uniq_paths[path] = uniq_paths.get(path,0)+1
        print("\n=== UNIQUE PATHS ===")
        for k,v in sorted(uniq_paths.items()):
            print(f"{k}: {v}")

        # also dump full HTML JS urls
        html = await page.content()
        # find script src that might contain business api
        scripts = re.findall(r'src="([^"]+\.js[^"]*)"', html)
        print("\n=== SCRIPT SRCS (first 10) ===")
        for s in scripts[:10]:
            print(s[:300])

        # also search html for api/business patterns
        hits = re.findall(r'api[^"\']{0,80}', html)
        uniq_hits = set(hits[:20])
        print("\n=== HTML api hits ===")
        for h in uniq_hits:
            print(h[:300])

        await browser.close()
        with open("/tmp/sniff_all.json","w",encoding="utf-8") as f:
            json.dump(captured, f, ensure_ascii=False, indent=2)
        print("saved /tmp/sniff_all.json")

if __name__ == "__main__":
    asyncio.run(main())
