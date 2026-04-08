import urllib.parse
import json
import requests
import time
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
        response = supabase.table('products').select('supplier_sku, name, brand, kaspi_sku, supplier_price').range(start, start + limit - 1).execute()
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

def query_ollama(db_name, db_price, candidates_text):
    prompt_text = f"""Ты парсер. Мой товар: {db_name}. Мой закуп: {db_price}. Вот 3 кандидата с Kaspi: 
{candidates_text}

Найди точное совпадение. Верни СТРОГО JSON: {{"best_sku": "артикул", "confidence": число_от_0_до_100}}. Если ничего не подходит, верни best_sku: null."""

    payload = {
        "model": "qwen2.5",
        "prompt": prompt_text,
        "format": "json",
        "stream": False,
        "temperature": 0.0
    }
    
    try:
        response = requests.post("http://localhost:11434/api/generate", json=payload, timeout=300)
        response.raise_for_status()
        data = response.json()
        result = json.loads(data.get("response", "{}"))
        return result
    except Exception as e:
        print(f"⚠️ Ошибка запроса к Ollama: {e}")
        return {"best_sku": None, "confidence": 0}

def main():
    items = get_unmatched_products()
    if not items:
        print("🎉 Все товары уже привязаны! Нет работы.")
        return

    print(f"🎯 Найдено {len(items)} товаров без артикулов. Запускаем браузер...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")
        
        for i, item in enumerate(items):
            db_name = f"{item.get('brand', '')} {item.get('name', '')}".strip()
            db_price = item.get('supplier_price', 0)
            
            print(f"\n[{i+1}/{len(items)}] 📦 Ищем: {db_name}")
            
            # пробелы кодируем как %20
            safe_query = urllib.parse.quote(db_name.replace("+", " "))
            search_url = f"https://kaspi.kz/shop/search/?text={safe_query}"
            
            page = context.new_page()
            
            # Блокируем лишние ресурсы для скорости и защищаемся от бана
            page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "font", "media"] else route.continue_())
            
            try:
                page.goto(search_url, wait_until="domcontentloaded")
                page.wait_for_timeout(3000) # пауза, чтобы Каспи не забанил
                
                try:
                    page.wait_for_selector('.item-card, .a-card, a[href*="/p/"]', timeout=5000)
                except Exception:
                    print("⚠️ Не найдены карточки товаров на странице поиска.")
                    page.close()
                    continue
                
                # Собираем уникальные ссылки первых 3 товаров
                links = page.locator('a[href*="/p/"]').element_handles()
                
                unique_urls = []
                for link in links:
                    href = link.get_attribute('href')
                    if href and '/p/' in href:
                        full_url = f"https://kaspi.kz{href}" if href.startswith('/') else href
                        clean_href = full_url.split('?')[0] # очищаем от параметров
                        if clean_href not in unique_urls:
                            unique_urls.append(clean_href)
                    if len(unique_urls) == 3:
                        break
                        
                if not unique_urls:
                    print("⚠️ Ссылки на товары не найдены.")
                    page.close()
                    continue
                    
                candidates = []
                
                for idx, url in enumerate(unique_urls):
                    page.goto(url, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000) # пауза перед чтением карточки
                    
                    try:
                        title_loc = page.locator('h1').first
                        title = title_loc.inner_text().strip() if title_loc.count() > 0 else "Нет названия"
                    except:
                        title = "Нет названия"
                        
                    try:
                        price_loc = page.locator('.item__price-once, .item__price').first
                        price = price_loc.inner_text().strip() if price_loc.count() > 0 else "Нет цены"
                    except:
                        price = "Нет цены"
                        
                    try:
                        desc_loc = page.locator('.item__description, .description, p').first
                        desc = desc_loc.inner_text().strip()[:300] if desc_loc.count() > 0 else ""
                    except:
                        desc = ""
                        
                    try:
                        spec_loc = page.locator('dl, .specifications, .item__specifications').first
                        specs = spec_loc.inner_text().strip()[:300] if spec_loc.count() > 0 else ""
                    except:
                        specs = ""
                        
                    sku = url.split('-')[-1].strip('/')
                    
                    candidate_text = f"{idx+1}:\nSKU: {sku}\nНазвание: {title}\nЦена: {price}\nОписание: {desc}\nХарактеристики: {specs}\n---\n"
                    candidates.append(candidate_text)
                    
                candidates_str = "\n".join(candidates)
                
                print(f"🤖 Передаем {len(candidates)} кандидатов нейросети Ollama...")
                ai_result = query_ollama(db_name, db_price, candidates_str)
                
                best_sku = ai_result.get("best_sku")
                confidence = ai_result.get("confidence", 0)
                
                if best_sku:
                    print(f"✅ Ollama выбрала SKU: {best_sku} с уверенностью {confidence}%! Сохраняю...")
                    try:
                        supabase.table('products').update({
                            "kaspi_sku": str(best_sku),
                            "ai_confidence": int(confidence),
                            "is_approved": False
                        }).eq("supplier_sku", item['supplier_sku']).execute()
                        print("💾 Сохранено!")
                    except Exception as e:
                        print(f"❌ Ошибка сохранения в БД: {e}")
                else:
                    print(f"⏭️ Ни один кандидат не подошел (Отказ от Ollama).")
                    
            except Exception as e:
                print(f"❌ Ошибка обработки товара: {e}")
            finally:
                page.close()
                time.sleep(2)

        print("\n🏁 Сессия маппинга завершена!")
        browser.close()

if __name__ == "__main__":
    main()
