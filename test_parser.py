import sys
import codecs
import json
import httpx
from bot import format_profile_data

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

def test_formatter():
    print("Testing API fetch & parsing...")
    email = "andrinromy@gmail.com"
    url = f"https://gmail-fetch-api-by-xeon.vercel.app/gmail?id={email}"
    
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url)
        assert resp.status_code == 200, f"Status code {resp.status_code}"
        data = resp.json()
        print("API Response fetched successfully:", data)
        
    caption, photo_url, markup = format_profile_data(data, email)
    print("\n--- FORMATTED OUTPUT ---")
    print("Photo URL:", photo_url)
    print("Caption:\n", caption)
    print("Markup buttons count:", len(markup.inline_keyboard) if markup else 0)
    print("--- TEST PASSED ---\n")

if __name__ == "__main__":
    test_formatter()
