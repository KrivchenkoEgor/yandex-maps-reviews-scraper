import asyncio
from playwright.async_api import async_playwright

OID="1033677441"
URL=f"https://yandex.ru/maps/?mode=poi&poi[uri]=ymapsbm1://org?oid={OID}&ll=82.956354,55.032128&poi[point]=82.956354,55.032128&z=13&tab=reviews"

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled","--no-sandbox"])
        ctx=await browser.new_context(user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36", viewport={"width":1280,"height":800}, locale="ru-RU", timezone_id="Asia/Novosibirsk")
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page=await ctx.new_page()
        page.on("response", lambda r: print(f"resp {r.url[:180]}") if "fetchReviews" in r.url else None)
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)
        try: await page.wait_for_selector("div.business-review-view", timeout=5000)
        except: pass
        # dump all possible filter elements
        html=await page.content()
        # look for rating-related
        import re
        # search for 5 stars filter
        cnt = await page.evaluate("""()=>{
            const els = Array.from(document.querySelectorAll('*'));
            const ratingEls = els.filter(e=> /\\b[1-5]\\s*★|\\bРейтинг|\\bОценка/.test(e.textContent.slice(0,100)));
            return ratingEls.slice(0,5).map(e=> ({text:e.textContent.slice(0,80), cls:e.className, html:e.outerHTML.slice(0,300)}));
        }""")
        print("rating candidates", cnt)
        # also look for select/popup for ranking
        ranking = await page.evaluate("""()=>{
            const els = Array.from(document.querySelectorAll('*'));
            const r = els.filter(e=> e.textContent.includes('По релевантности') || e.textContent.includes('По времени') || e.textContent.includes('По рейтингу'));
            return r.slice(0,5).map(e=> e.textContent.slice(0,80));
        }""")
        print("ranking", ranking)
        # try to find ranking dropdown
        loc = page.locator("text=По релевантности").first
        print(f"ranking locator {await loc.count()}")
        if await loc.count()>0:
            await loc.click()
            await page.wait_for_timeout(2000)
            opts = await page.evaluate("""()=>{
                return Array.from(document.querySelectorAll('*')).filter(e=> e.textContent.includes('По ')).map(e=> e.textContent.slice(0,80))
            }""")
            print("after click ranking opts", opts[:10])
        await browser.close()

if __name__=="__main__":
    asyncio.run(main())
