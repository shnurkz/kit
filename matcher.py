import urllib.parse
from supabase import create_client, Client, ClientOptions
from playwright.sync_api import sync_playwright

# Доступы к Supabase (используем service_role ключ)
SUPABASE_URL = "https://akrygxdwrwyoaxdsjefs.supabase.co"
SUPABASE_KEY = "sb_secret_-6yiRL3AwNCJ3EHqtPW-ww_oiVqN6f_"
opts = ClientOptions(postgrest_client_timeout=15)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY, options=opts)

def get_unmatched_products():
    print("📥 Скачиваем всю базу (обходим лимит в 1000 строк)...")
    all_data = []
    start = 0
    limit = 1000
    
    while True:
        response = supabase.table('products').select('supplier_sku, name, brand, kaspi_sku').range(start, start + limit - 1).execute()
        data = response.data
        if not data:
            break
        all_data.extend(data)
        if len(data) < limit:
            break
        start += limit
        
    unmatched = []
    for row in all_data:
        sku = row.get('kaspi_sku')
        if not sku or str(sku).strip() == '' or str(sku).lower() == 'none' or str(sku).lower() == 'nan':
            unmatched.append(row)
            
    return unmatched

def main():
    items = get_unmatched_products()
    if not items:
        print("🎉 Все товары уже привязаны! Нет работы.")
        return

    print(f"🎯 Найдено {len(items)} товаров без артикулов. Запускаем браузер...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        
        base_page = context.new_page()
        base_page.goto("about:blank")
        base_page.evaluate("document.body.innerHTML = '<h1 style=\"font-family:sans-serif; padding: 20px;\">Не закрывай эту вкладку!</h1><p style=\"font-family:sans-serif; padding: 0 20px;\">Она держит браузер открытым. Рабочие вкладки будут открываться рядом.</p>'")

        for i, item in enumerate(items):
            print(f"\n[{i+1}/{len(items)}] 📦 Ищем: {item.get('brand', '')} {item.get('name', '')}")
            
            query = f"{item.get('brand', '')} {item.get('name', '')}".strip()
            safe_query = urllib.parse.quote_plus(query)
            search_url = f"https://kaspi.kz/shop/search/?text={safe_query}"
            
            work_page = context.new_page()
            try:
                work_page.goto(search_url)
            except:
                pass
            
            print("⏳ Ожидание... (Кликни на товар или ЗАКРОЙ ВЛАДКУ для пропуска)")
            
            sku_found = None
            
            while True:
                base_page.wait_for_timeout(500) 
                
                pages = context.pages
                
                if len(pages) <= 1:
                    print("⏭️ Пропущено (вкладка закрыта).")
                    break
                    
                for page in pages:
                    if page == base_page:
                        continue
                    
                    try:
                        url = page.url
                        if '/shop/p/' in url:
                            clean_url = url.split('?')[0].strip('/')
                            possible_sku = clean_url.split('-')[-1]
                            
                            if possible_sku.isdigit():
                                sku_found = possible_sku
                                break
                    except:
                        pass 
                        
                if sku_found:
                    break
            
            if sku_found:
                print(f"✅ Пойман артикул: {sku_found}! Записываю...")
                try:
                    supabase.table('products').update({"kaspi_sku": sku_found}).eq("supplier_sku", item['supplier_sku']).execute()
                    print("💾 Успешно сохранено.")
                except Exception as e:
                    print(f"❌ Ошибка сохранения: {e}")
            
            for page in context.pages:
                if page != base_page:
                    try:
                        page.close()
                    except:
                        pass

        print("\n🏁 Сессия маппинга завершена!")
        browser.close()

if __name__ == "__main__":
    main()
