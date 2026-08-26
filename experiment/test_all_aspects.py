import asyncio, json
from playwright.async_api import async_playwright

OID="1033677441"
URL=f"https://yandex.ru/maps/?mode=poi&poi[uri]=ymapsbm1://org?oid={OID}&ll=82.956354,55.032128&poi[point]=82.956354,55.032128&z=13&tab=reviews"

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled","--no-sandbox"])
        ctx=await browser.new_context(user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36", viewport={"width":1280,"height":800}, locale="ru-RU", timezone_id="Asia/Novosibirsk")
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page=await ctx.new_page()
        captures=[]
        async def h(resp):
            if "fetchReviews" in resp.url and "aspectId" in resp.url:
                try:
                    j=await resp.json()
                    data=j.get("data",{})
                    params=data.get("params",{})
                    print(f"[ASPECT FETCH] {resp.url[:300]} total={params.get('count') or data.get('count')} got={len(data.get('reviews',[]))} aspectId={params.get('aspectId') or 'N/A'}")
                    captures.append({"url":resp.url, "count":params.get("count"), "got":len(data.get('reviews',[]))})
                except: pass
            elif "fetchReviews" in resp.url:
                try:
                    j=await resp.json()
                    data=j.get("data",{})
                    params=data.get("params",{})
                    print(f"[GENERAL FETCH] page={params.get('page')} total={params.get('count')} got={len(data.get('reviews',[]))}")
                except: pass
        page.on("response", lambda r: asyncio.create_task(h(r)))
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)
        try: await page.wait_for_selector("div.business-review-view", timeout=8000)
        except: pass
        await page.wait_for_timeout(2000)
        # get aspect texts via evaluate
        aspect_texts = ["Выбор товаров","Еда","Качество товаров","Скидки и акции"]
        for txt in aspect_texts:
            print(f"\n--- clicking {txt} ---")
            loc = page.locator(f"text={txt}").first
            cnt = await loc.count()
            print(f" locator count {cnt}")
            if cnt==0:
                continue
            await loc.click()
            await page.wait_for_timeout(3500)
            # also click again to deselect? we want capture
        # try to get all aspectIds from JS
        # after clicks, try to dump captures
        print("\n=== SUMMARY ===")
        for c in captures:
            print(c)
        # also try to get aspectIds via JS state
        # try to find in window state
        html = await page.content()
        # search for aspectId in html
        import re
        ids = re.findall(r'aspectId["\']?\s*[:=]\s*["\']?(\d+)', html)
        print(f"ids in html: {set(ids)}")
        # also try page evaluate to get aspect mapping
        mapping = await page.evaluate("""()=>{
            // try to find in __INITIAL_STATE__ or similar
            const scripts = Array.from(document.querySelectorAll('script'));
            for(const s of scripts){
                const t=s.textContent||'';
                if(t.includes('aspectId')){
                    return t.slice(t.indexOf('aspectId')-200, t.indexOf('aspectId')+300);
                }
            }
            return null;
        }""")
        print(f"mapping snippet: {mapping}")

        await browser.close()

if __name__=="__main__":
    asyncio.run(main())
