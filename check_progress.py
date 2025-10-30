import time
import sys

print("Monitoring matrix test progress...")
print("Press Ctrl+C to stop monitoring\n")

try:
    while True:
        try:
            with open('results/matrix_price_filter_optimization.xlsx', 'rb') as f:
                size = len(f.read())
                print(f"\r✅ Test completed! File size: {size:,} bytes", end='', flush=True)
                print("\n\nTest finished successfully!")
                break
        except FileNotFoundError:
            print("\r⏳ Test running... (waiting for results file)", end='', flush=True)
        
        time.sleep(5)
except KeyboardInterrupt:
    print("\n\nMonitoring stopped by user")
    sys.exit(0)
