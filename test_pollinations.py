import requests
import time

print("Testing Pollinations.ai...")

# Test 1: Simple test
print("\n1. Testing with 512x512...")
try:
    resp = requests.get(
        'https://gen.pollinations.ai/image/a beautiful sunset',
        params={'model': 'flux', 'width': 512, 'height': 512},
        timeout=60
    )
    print(f"   Status: {resp.status_code}")
    print(f"   Content-Type: {resp.headers.get('Content-Type', 'unknown')}")
    print(f"   Size: {len(resp.content)} bytes")
    if resp.status_code == 200 and len(resp.content) > 1000:
        print("   SUCCESS!")
    else:
        print("   FAILED - Response too small or error")
        print(f"   Response text: {resp.text[:200]}")
except Exception as e:
    print(f"   ERROR: {e}")

time.sleep(2)

# Test 2: Larger size
print("\n2. Testing with 1024x1024...")
try:
    resp = requests.get(
        'https://gen.pollinations.ai/image/a mountain landscape',
        params={'model': 'flux', 'width': 1024, 'height': 1024},
        timeout=60
    )
    print(f"   Status: {resp.status_code}")
    print(f"   Content-Type: {resp.headers.get('Content-Type', 'unknown')}")
    print(f"   Size: {len(resp.content)} bytes")
    if resp.status_code == 200 and len(resp.content) > 1000:
        print("   SUCCESS!")
    else:
        print("   FAILED")
        print(f"   Response text: {resp.text[:200]}")
except Exception as e:
    print(f"   ERROR: {e}")

# Test 3: Very large size (what the bot uses)
print("\n3. Testing with 2048x2048...")
try:
    resp = requests.get(
        'https://gen.pollinations.ai/image/a city skyline',
        params={'model': 'flux', 'width': 2048, 'height': 2048},
        timeout=90
    )
    print(f"   Status: {resp.status_code}")
    print(f"   Content-Type: {resp.headers.get('Content-Type', 'unknown')}")
    print(f"   Size: {len(resp.content)} bytes")
    if resp.status_code == 200 and len(resp.content) > 1000:
        print("   SUCCESS!")
    else:
        print("   FAILED")
        print(f"   Response text: {resp.text[:200]}")
except Exception as e:
    print(f"   ERROR: {e}")
