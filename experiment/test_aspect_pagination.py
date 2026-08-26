import asyncio, json
from urllib.parse import urlparse, parse_qs, urlencode
from playwright.async_api import async_playwright

OID="1033677441"
URL=f"https://yandex.ru/maps/?mode=poi&poi[uri]=ymapsbm1://org?oid={OID}&ll=82.956354,55.032128&poi[point]=82.956354,55.032128&z=13&tab=reviews"

ASPECTS = [
    ("Выбор товаров", "3502044705", 267),
    ("Еда", "3502043738", 261),
    ("Качество товаров", "3502044673", 196),
    ("Скидки и акции", "3502044260", 153),
]

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled","--no-sandbox"])
        ctx=await browser.new_context(user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36", viewport={"width":1280,"height":800}, locale="ru-RU", timezone_id="Asia/Novosibirsk")
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page=await ctx.new_page()
        base=None
        async def h(resp):
            nonlocal base
            if "fetchReviews" in resp.url and "aspectId" not in resp.url and base is None:
                base=resp.url
        page.on("response", lambda r: asyncio.create_task(h(r)))
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)
        try: await page.wait_for_selector("div.business-review-view", timeout=8000)
        except: pass
        await page.wait_for_timeout(3000)
        if not base:
            print("no base"); await browser.close(); return
        parsed=urlparse(base)
        # collect all reviews deduplicated
        all_reviews={}
        for name, aid, expected in ASPECTS:
            print(f"\n=== Aspect {name} id={aid} expected {expected} ===")
            for page_num in range(1,7): # up to 6 pages for 267
                qs=parse_qs(parsed.query)
                flat={k:v[0] for k,v in qs.items()}
                flat["aspectId"]=aid
                flat["ranking"]="by_aspect_tone_desc"
                flat["page"]=str(page_num)
                flat["pageSize"]="50"
                test_url=f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(flat)}"
                res=await page.evaluate("""async (url)=>{
                    try{
                        const r=await fetch(url,{credentials:'include'});
                        const text=await r.text();
                        let j; try{j=JSON.parse(text);}catch(e){return {status:r.status, text:text.slice(0,200), isJson:false};}
                        const d=j.data||{};
                        const reviews=d.reviews||[];
                        return {status:r.status, got:reviews.length, total:(d.params||{}).count || d.count || null, reviews:reviews.map(x=>x.reviewId).slice(0,3)}
                    }catch(e){return {error:e.toString()}}
                }""", test_url)
                print(f" page {page_num} -> {res}")
                # also collect via direct fetch json for dedup
                # do second fetch to get full reviews for dedup count
                # we need to actually get reviews
                detailed = await page.evaluate("""async (url)=>{
                    const r=await fetch(url,{credentials:'include'});
                    const j=await r.json();
                    return j.data.reviews.map(x=>x.reviewId)
                }""", test_url)
                for rid in detailed:
                    all_reviews[rid]=True
                await asyncio.sleep(0.3)
                if res.get("got",0)<50:
                    break
        print(f"\n=== COMBINED UNIQUE across aspects ===")
        print(f"total unique collected so far: {len(all_reviews)} (expected up to 835)")
        # now also fetch general pages 1..12 without aspect
        print("\n=== Fetch general pages 1..12 (no aspect) ===")
        general_ids=set()
        for pn in range(1,13):
            qs=parse_qs(parsed.query)
            flat={k:v[0] for k,v in qs.items()}
            if "aspectId" in flat: del flat["aspectId"]
            flat["ranking"]="by_relevance_org"
            flat["page"]=str(pn)
            flat["pageSize"]="50"
            test_url=f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(flat)}"
            ids=await page.evaluate("""async (url)=>{
                const r=await fetch(url,{credentials:'include'});
                const j=await r.json();
                return j.data.reviews.map(x=>x.reviewId)
            }""", test_url)
            for rid in ids: general_ids.add(rid)
            await asyncio.sleep(0.2)
        print(f"general unique 12 pages: {len(general_ids)}")
        # combine
        combined = set(all_reviews.keys()) | general_ids
        print(f"combined general+aspects unique: {len(combined)}")
        # check if combined reaches 835
        # also check remaining = 835 - combined
        print(f"835 - combined = {835 - len(combined)}")

        await browser.close()

if __name__=="__main__":
    asyncio.run(main())
