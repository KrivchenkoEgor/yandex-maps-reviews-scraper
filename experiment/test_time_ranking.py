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
        fetches=[]
        async def h(resp):
            if "fetchReviews" in resp.url:
                try:
                    j=await resp.json()
                    data=j.get("data",{})
                    params=data.get("params",{})
                    reviews=data.get("reviews",[])
                    # detect ranking from url
                    from urllib.parse import urlparse, parse_qs
                    qs=parse_qs(urlparse(resp.url).query)
                    ranking=qs.get("ranking",["?"])[0]
                    # dates of first 2 reviews
                    dates=[r.get("updatedTime","")[:10] for r in reviews[:2]]
                    print(f"[FETCH] ranking={ranking} page={params.get('page')} got={len(reviews)} dates={dates} total={params.get('count')}")
                    fetches.append({"ranking":ranking, "got":len(reviews), "dates":dates, "url":resp.url})
                except Exception as e:
                    print(f"h err {e}")
        page.on("response", lambda r: asyncio.create_task(h(r)))
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)
        try: await page.wait_for_selector("div.business-review-view", timeout=8000)
        except: pass
        await page.wait_for_timeout(2000)
        print(f"initial fetches {len(fetches)}")
        # try to find ranking dropdown and click "По времени"
        # from JS we know ranking view class
        # look for button with text По релевантности
        loc = page.locator("text=По релевантности").first
        cnt = await loc.count()
        print(f"ranking dropdown locator count {cnt}")
        if cnt>0:
            await loc.click()
            await page.wait_for_timeout(1500)
            # now look for options
            opts = await page.evaluate("""()=>{
                const els=Array.from(document.querySelectorAll('*')).filter(e=> e.textContent.trim()==='По времени' || e.textContent.trim()==='По рейтингу');
                return els.map(e=> ({text:e.textContent.trim(), html:e.outerHTML.slice(0,400)}));
            }""")
            print(f"ranking options after click: {opts}")
            # click По времени
            loc_time = page.locator("text=По времени").first
            cnt2 = await loc_time.count()
            print(f"По времени locator {cnt2}")
            if cnt2>0:
                await loc_time.click()
                print("clicked По времени")
                await page.wait_for_timeout(4000)
                print(f"fetches after time click: {len(fetches)}")
                for f in fetches[-3:]:
                    print(f)
                # scroll a bit to get second page of by_time
                for i in range(3):
                    await page.evaluate("""()=>{
                        const els=document.querySelectorAll('div[class*="scroll"]');
                        let best=null,maxH=0; for(const el of els){ if(el.scrollHeight>el.clientHeight && el.scrollHeight>maxH){maxH=el.scrollHeight; best=el;}}
                        if(best) best.scrollTop=best.scrollHeight; else window.scrollTo(0, document.body.scrollHeight);
                    }""")
                    await page.mouse.wheel(0,800)
                    await page.wait_for_timeout(2500)
                print(f"after scroll fetches {len(fetches)}")
                for f in fetches[-5:]:
                    print(f)
            else:
                print("По времени not found")
        else:
            print("no ranking dropdown")
        # also try direct check: what ranking values are available after click
        # dump ranking popup text
        popup = await page.evaluate("""()=>{
            return document.documentElement.innerHTML.slice(0,8000)
        }""")
        # save
        with open("/tmp/time_ranking_test.json","w",encoding="utf-8") as f:
            json.dump(fetches,f,ensure_ascii=False,indent=2)
        await browser.close()

if __name__=="__main__":
    asyncio.run(main())
