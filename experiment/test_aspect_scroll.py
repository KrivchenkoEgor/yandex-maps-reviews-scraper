import asyncio, json
from playwright.async_api import async_playwright

OID="1033677441"
URL=f"https://yandex.ru/maps/?mode=poi&poi[uri]=ymapsbm1://org?oid={OID}&ll=82.956354,55.032128&poi[point]=82.956354,55.032128&z=13&tab=reviews"

ASPECTS = ["Выбор товаров","Еда","Качество товаров","Скидки и акции"]

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled","--no-sandbox"])
        ctx=await browser.new_context(user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36", viewport={"width":1280,"height":800}, locale="ru-RU", timezone_id="Asia/Novosibirsk")
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page=await ctx.new_page()
        all_ids=set()
        general_ids=set()

        async def h(resp):
            if "fetchReviews" in resp.url:
                try:
                    j=await resp.json()
                    data=j.get("data",{})
                    params=data.get("params",{})
                    reviews=data.get("reviews",[])
                    url=resp.url
                    print(f"[FETCH] page={params.get('page')} aspectId={'yes' if 'aspectId' in url else 'no'} total={params.get('count')} got={len(reviews)} ranking={('by_aspect' if 'aspectId' in url else 'by_relevance')}")
                    for r in reviews:
                        all_ids.add(r.get("reviewId"))
                        if "aspectId" not in url:
                            general_ids.add(r.get("reviewId"))
                except: pass
        page.on("response", lambda r: asyncio.create_task(h(r)))

        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)
        try: await page.wait_for_selector("div.business-review-view", timeout=8000)
        except: pass
        await page.wait_for_timeout(2000)
        print(f"initial all_ids {len(all_ids)}")

        # scroll general 12 pages first to get 600
        for i in range(12):
            await page.evaluate("""()=>{
                const els=document.querySelectorAll('div[class*="scroll"]');
                let best=null,maxH=0; for(const el of els){ if(el.scrollHeight>el.clientHeight && el.scrollHeight>maxH){maxH=el.scrollHeight; best=el;}}
                if(best) best.scrollTop=best.scrollHeight; else window.scrollTo(0, document.body.scrollHeight);
            }""")
            await page.mouse.wheel(0,800)
            await page.wait_for_timeout(2500)
        print(f"after general scroll all_ids={len(all_ids)} general={len(general_ids)}")

        # now per aspect
        for txt in ASPECTS:
            print(f"\n=== Click aspect {txt} ===")
            loc=page.locator(f"text={txt}").first
            if await loc.count()==0:
                print("not found")
                continue
            await loc.click()
            await page.wait_for_timeout(3000)
            # scroll within aspect
            for i in range(7):
                await page.evaluate("""()=>{
                    const els=document.querySelectorAll('div[class*="scroll"]');
                    let best=null,maxH=0; for(const el of els){ if(el.scrollHeight>el.clientHeight && el.scrollHeight>maxH){maxH=el.scrollHeight; best=el;}}
                    if(best) best.scrollTop=best.scrollHeight; else window.scrollTo(0, document.body.scrollHeight);
                }""")
                await page.mouse.wheel(0,800)
                await page.wait_for_timeout(2500)
                print(f"  scroll {i+1} all_ids={len(all_ids)}")
            # click again to deselect? need to reset to general before next aspect
            # click same aspect again to toggle off, or click "Все" ?
            # try clicking active aspect again
            await loc.click()
            await page.wait_for_timeout(2000)

        print(f"\n=== FINAL ===")
        print(f"all unique across general+aspects: {len(all_ids)}")
        print(f"general only: {len(general_ids)}")
        print(f"835 - all = {835 - len(all_ids)}")
        # save
        with open("/tmp/aspect_scroll_result.json","w") as f:
            json.dump({"all":len(all_ids), "general":len(general_ids)}, f)

        await browser.close()

if __name__=="__main__":
    asyncio.run(main())
