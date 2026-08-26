import asyncio, json
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
        aspects_data=None
        async def h(resp):
            nonlocal base, aspects_data
            if "fetchReviews" in resp.url:
                try:
                    j=await resp.json()
                    if base is None:
                        base=resp.url
                        print("base", base[:250])
                    data=j.get("data",{})
                    if "aspects" in data and aspects_data is None:
                        aspects_data=data.get("aspects")
                        print("aspects", json.dumps(aspects_data, ensure_ascii=False)[:1000])
                    # also check params for aspectId echo
                    # print aspects if present
                except: pass
        page.on("response", lambda r: asyncio.create_task(h(r)))
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)
        try: await page.wait_for_selector("div.business-review-view", timeout=8000)
        except: pass
        await page.wait_for_timeout(3000)
        if not base:
            print("no base"); await browser.close(); return
        parsed=urlparse(base)
        if aspects_data is None:
            print("no aspects data captured, trying to parse from page")
            # try to get from page evaluate
            aspects_data = await page.evaluate("""()=>{
                // try to find aspects in window state
                return document.documentElement.innerHTML.slice(0,5000);
            }""")
            print(aspects_data[:2000])
            await browser.close(); return
        print(f"found {len(aspects_data)} aspects")
        # try each aspect
        for asp in aspects_data:
            aid = asp.get("id")
            name = asp.get("text") or asp.get("name") or str(asp)[:50]
            print(f"\n=== Testing aspect {aid} : {name} ===")
            for ranking in ["by_relevance_org","by_time","by_rating_asc","by_rating_desc","by_aspect_tone_asc","by_aspect_tone_desc"]:
                try:
                    qs=parse_qs(parsed.query)
                    flat={k:v[0] for k,v in qs.items()}
                    flat["aspectId"]=str(aid)
                    flat["ranking"]=ranking
                    flat["page"]="1"
                    flat["pageSize"]="50"
                    test_url=f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(flat)}"
                    res=await page.evaluate("""async (url)=>{
                        try{
                            const r=await fetch(url,{credentials:'include'});
                            const text=await r.text();
                            let j; try{j=JSON.parse(text);}catch(e){return {status:r.status, text:text.slice(0,200), isJson:false};}
                            const d=j.data||{};
                            return {status:r.status, total:d.total||d.count|| (d.params||{}).count || null, got:(d.reviews||[]).length, aspects:(d.aspects||[]).length, keys:Object.keys(d)}
                        }catch(e){return {error:e.toString()}}
                    }""", test_url)
                    print(f" ranking={ranking} -> {res}")
                    await asyncio.sleep(0.4)
                except Exception as e:
                    print(e)
            # also test page13 with aspect
            try:
                qs=parse_qs(parsed.query)
                flat={k:v[0] for k,v in qs.items()}
                flat["aspectId"]=str(aid)
                flat["ranking"]="by_relevance_org"
                flat["page"]="13"
                flat["pageSize"]="50"
                test_url=f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(flat)}"
                res=await page.evaluate("""async (url)=>{
                    try{
                        const r=await fetch(url,{credentials:'include'});
                        const text=await r.text();
                        let j; try{j=JSON.parse(text);}catch(e){return {status:r.status, text:text.slice(0,200), isJson:false};}
                        const d=j.data||{};
                        return {status:r.status, total:d.total||d.count|| (d.params||{}).count || null, got:(d.reviews||[]).length}
                    }catch(e){return {error:e.toString()}}
                }""", test_url)
                print(f" aspect {aid} page13 -> {res}")
            except Exception as e:
                print(e)

        await browser.close()

if __name__=="__main__":
    asyncio.run(main())
