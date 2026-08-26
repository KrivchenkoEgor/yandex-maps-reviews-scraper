"""
Experiment 2: test all ranking values with page=1
"""
import asyncio, json
from urllib.parse import urlparse, parse_qs, urlencode
from playwright.async_api import async_playwright

OID = "1033677441"
URL = f"https://yandex.ru/maps/?mode=poi&poi[uri]=ymapsbm1://org?oid={OID}&ll=82.956354,55.032128&poi[point]=82.956354,55.032128&z=13&tab=reviews"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled","--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
            viewport={"width":1280,"height":800},
            locale="ru-RU",
            timezone_id="Asia/Novosibirsk",
        )
        await context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = await context.new_page()
        fetches=[]
        async def handle(resp):
            if "fetchReviews" in resp.url:
                try:
                    j=await resp.json()
                    data=j.get("data",{})
                    total=data.get("total") or data.get("count")
                    revs=data.get("reviews",[])
                    parsed=urlparse(resp.url)
                    qs=parse_qs(parsed.query)
                    ranking=qs.get("ranking",["?"])[0]
                    page_num=qs.get("page",["?"])[0]
                    print(f"[orig] page={page_num} ranking={ranking} total={total} got={len(revs)}")
                    fetches.append(resp.url)
                except: pass
        page.on("response", lambda r: asyncio.create_task(handle(r)))
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(4000)
        try:
            await page.wait_for_selector("div.business-review-view", timeout=8000)
        except: pass
        await page.wait_for_timeout(2000)
        if not fetches:
            print("no fetches captured")
            await browser.close()
            return
        base_url = fetches[0]
        parsed=urlparse(base_url)
        # collect candidates from page source
        html=await page.content()
        # search for ranking strings in html/js
        import re
        rankings_in_html=set(re.findall(r'ranking["\']?\s*[:=]\s*["\']([^"\']+)["\']', html))
        rankings_in_html.update(re.findall(r'by_\w+', html))
        print(f"rankings found in html: {rankings_in_html}")
        candidates = ["by_relevance_org","by_time","by_rating","by_relevance","recent","time","rating","date","newest","oldest","by_time_desc","by_time_asc","by_rating_desc","by_rating_asc","relevance"]
        candidates = list(set(candidates) | rankings_in_html)
        print(f"testing candidates: {candidates}")
        # try each ranking with page=1
        for ranking in candidates:
            # also try without ranking param (remove)
            for page_num in [1,13]:
                try:
                    # build url from base, replace ranking and page
                    qs=parse_qs(parsed.query)
                    new_qs={}
                    for k,v in qs.items():
                        new_qs[k]=v[0]
                    new_qs["ranking"]=ranking
                    new_qs["page"]=str(page_num)
                    new_qs["pageSize"]="50"
                    # keep csrfToken etc
                    test_url=f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(new_qs)}"
                    result=await page.evaluate("""async (url)=>{
                        try{
                            const r=await fetch(url,{credentials:'include'});
                            const text=await r.text();
                            let j; try{j=JSON.parse(text);}catch(e){return {status:r.status, text:text.slice(0,200), isJson:false};}
                            const d=j.data||{};
                            return {status:r.status, isJson:true, total:d.total||d.count||null, got:(d.reviews||[]).length, keys:Object.keys(d).slice(0,10), url:url}
                        }catch(e){return {error:e.toString()}}
                    }""", test_url)
                    print(f"try ranking={ranking} page={page_num} -> {result}")
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"err {ranking} page{page_num} {e}")
        # also try pageSize variations
        print("\n=== pageSize variations with by_relevance_org page1 ===")
        for ps in [10,20,50,100]:
            try:
                qs=parse_qs(parsed.query)
                new_qs={k:v[0] for k,v in qs.items()}
                new_qs["ranking"]="by_relevance_org"
                new_qs["page"]="1"
                new_qs["pageSize"]=str(ps)
                test_url=f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(new_qs)}"
                result=await page.evaluate("""async (url)=>{
                    try{
                        const r=await fetch(url,{credentials:'include'});
                        const text=await r.text();
                        let j; try{j=JSON.parse(text);}catch(e){return {status:r.status, text:text.slice(0,200), isJson:false};}
                        const d=j.data||{};
                        return {status:r.status, total:d.total||d.count||null, got:(d.reviews||[]).length}
                    }catch(e){return {error:e.toString()}}
                }""", test_url)
                print(f"pageSize {ps} -> {result}")
                await asyncio.sleep(0.5)
            except Exception as e:
                print(e)
        # try to find alternative endpoint: maybe business/fetchReviews with offset instead of page?
        print("\n=== check for any other review endpoint in page html ===")
        # search for fetchReviews in page scripts
        # dump a bit of html around
        # already have html, look for api/business
        import re as re2
        for m in re2.finditer(r'api/business/[^\s"\']+', html):
            print(m.group(0)[:200])
            if len(list(re2.finditer(r'api/business/[^\s"\']+', html)))>5: break

        await browser.close()

if __name__=="__main__":
    asyncio.run(main())
