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
        captured=[]
        async def h(resp):
            if "fetchReviews" in resp.url:
                try:
                    j=await resp.json()
                    data=j.get("data",{})
                    print(f"[fetch] {resp.url[:250]} total={(data.get('params') or {}).get('count') or data.get('count')} got={len(data.get('reviews',[]))}")
                    captured.append(resp.url)
                except: pass
        page.on("response", lambda r: asyncio.create_task(h(r)))
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)
        try: await page.wait_for_selector("div.business-review-view", timeout=8000)
        except: pass
        await page.wait_for_timeout(2000)
        # find aspect tags
        # try selectors from earlier
        for sel in ["div[class*='business-aspect']", "div[class*='aspect']", "[class*='aspect-view']", "div[class*='business-review-aspects']"]:
            cnt=await page.evaluate(f"""(sel)=>document.querySelectorAll(sel).length""", sel)
            print(f"sel {sel} count {cnt}")
        # dump aspects HTML
        html=await page.content()
        # find aspect texts
        import re
        # look for aspect tags
        aspects = await page.evaluate("""()=>{
            const els = Array.from(document.querySelectorAll('*')).filter(e=> e.textContent && ['Выбор товаров','Еда','Качество товаров','Скидки и акции'].includes(e.textContent.trim()));
            return els.map(e=> ({text:e.textContent.trim(), html:e.outerHTML.slice(0,300), cls:e.className}));
        }""")
        print("aspects found via text search:", json.dumps(aspects, ensure_ascii=False))
        # try clicking first aspect
        # find clickable aspect element
        clickable = await page.query_selector_all("div[class*='aspect']")
        print(f"clickable aspect candidates {len(clickable)}")
        for i, el in enumerate(clickable[:4]):
            try:
                txt = await el.inner_text()
                print(f"  {i}: {txt[:80]} class={await el.get_attribute('class')}")
            except: pass
        # try to click first with text "Выбор товаров"
        try:
            el = await page.evaluate_handle("""()=>{
                const els = Array.from(document.querySelectorAll('*'));
                for(const e of els){ if(e.textContent.trim()==='Выбор товаров'){ return e; } }
                return null;
            }""")
            # need to get as element handle
            # alternative: use locator
            loc = page.locator("text=Выбор товаров").first
            print(f"locator count {await loc.count()}")
            if await loc.count()>0:
                await loc.click()
                print("clicked Выбор товаров")
                await page.wait_for_timeout(4000)
                print(f"after click captured {len(captured)}")
                for u in captured[-3:]:
                    print(u[:300])
        except Exception as e:
            print(f"click err {e}")
        # also try clicking via JS
        # second try
        print("done")
        await browser.close()

if __name__=="__main__":
    asyncio.run(main())
