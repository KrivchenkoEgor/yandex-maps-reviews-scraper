import asyncio
from urllib.parse import urlparse, parse_qs, urlencode
from playwright.async_api import async_playwright

OID="1033677441"
URL=f"https://yandex.ru/maps/?mode=poi&poi[uri]=ymapsbm1://org?oid={OID}&ll=82.956354,55.032128&poi[point]=82.956354,55.032128&z=13&tab=reviews"

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled","--no-sandbox"])
        ctx=await browser.new_context(user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36", viewport={"width":1280,"height":800}, locale="ru-RU", timezone_id="Asia/Novosibirsk")
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page=await ctx.new_page()
        base=None
        async def h(resp):
            nonlocal base
            if "fetchReviews" in resp.url and base is None:
                base=resp.url
                print("captured base", base[:200])
        page.on("response", lambda r: asyncio.create_task(h(r)))
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)
        try: await page.wait_for_selector("div.business-review-view", timeout=5000)
        except: pass
        await page.wait_for_timeout(2000)
        if not base:
            print("no base")
            await browser.close(); return
        parsed=urlparse(base)
        # tests
        tests = [
            ("no ranking", lambda qs: {k:v for k,v in qs.items() if k!="ranking"}),
            ("offset 600", lambda qs: {**qs, "offset":"600"}),
            ("offset 600 + page13", lambda qs: {**qs, "page":"13", "offset":"600"}),
            ("page 13 only", lambda qs: {**qs, "page":"13"}),
            ("pageSize 100 ranking", lambda qs: {**qs, "pageSize":"100"}),
            ("businessId only page13", lambda qs: {"ajax":"1","businessId":qs.get("businessId",[""])[0],"page":"13","pageSize":"50","ranking":"by_relevance_org"}),
        ]
        for name, fn in tests:
            try:
                qs=parse_qs(parsed.query)
                flat={k:v[0] for k,v in qs.items()}
                new_flat=fn(flat)
                # ensure locale etc
                test_url=f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(new_flat)}"
                print(f"\nTest {name}: {test_url[:200]}")
                res=await page.evaluate("""async (url)=>{
                    try{
                        const r=await fetch(url,{credentials:'include'});
                        const text=await r.text();
                        let j; try{j=JSON.parse(text);}catch(e){return {status:r.status, text:text.slice(0,300), isJson:false};}
                        const d=j.data||{};
                        return {status:r.status, total:d.total||d.count|| (d.params||{}).count || null, got:(d.reviews||[]).length, keys:Object.keys(d)}
                    }catch(e){return {error:e.toString()}}
                }""", test_url)
                print(f" -> {res}")
                await asyncio.sleep(0.5)
            except Exception as e:
                print(e)
        # also check DOM count after full scroll via parse
        print("\n=== DOM check after scroll ===")
        # do scroll a bit and parse
        for i in range(5):
            await page.evaluate("""()=>{
                const els=document.querySelectorAll('div[class*="scroll"]');
                let best=null,maxH=0; for(const el of els){ if(el.scrollHeight>el.clientHeight && el.scrollHeight>maxH){maxH=el.scrollHeight; best=el;}}
                if(best) best.scrollTop=best.scrollHeight; else window.scrollTo(0, document.body.scrollHeight);
            }""")
            await page.wait_for_timeout(2000)
        html=await page.content()
        # count review cards
        cnt=await page.evaluate("""()=>document.querySelectorAll('div.business-review-view').length""")
        print(f"DOM cards count after scroll: {cnt}")
        await browser.close()

if __name__=="__main__":
    asyncio.run(main())
