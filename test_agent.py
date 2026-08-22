#!/usr/bin/env python3
"""Simple test agent for admin console demo."""
import sys
import time
import random

def main():
    agent_type = sys.argv[1] if len(sys.argv) > 1 else "refresh"

    print(f"[INFO] Starting {agent_type} agent...")
    time.sleep(0.5)

    print("[INFO] Loading configuration...")
    time.sleep(0.5)

    print("[INFO] Connecting to data sources...")
    time.sleep(0.5)

    if agent_type == "refresh":
        items = random.randint(50, 200)
        print(f"[INFO] Processing {items} opportunities...")
        time.sleep(1.5)
        print(f"[INFO] Updated metadata for {items} items")
        print(f"[INFO] Skipped stale: 12, Missing: 8")
        time.sleep(0.5)
        print("[SUCCESS] Agent completed successfully")
        sys.exit(0)

    elif agent_type == "scraper":
        print("[INFO] Fetching 40 Angles database...")
        time.sleep(1)
        new_opps = random.randint(30, 80)
        print(f"[INFO] Found {new_opps} new opportunities")
        print(f"[INFO] Validated {new_opps} entries")
        time.sleep(0.5)
        print("[SUCCESS] Scraper completed - added to database")
        sys.exit(0)

    elif agent_type == "deadline":
        print("[INFO] Checking deadlines for active opportunities...")
        checked = random.randint(100, 300)
        print(f"[INFO] Checked {checked} opportunities")
        updated = random.randint(5, 30)
        print(f"[INFO] Updated {updated} deadlines")
        time.sleep(1)
        print("[SUCCESS] Deadline check completed")
        sys.exit(0)

    else:
        print("[ERROR] Unknown agent type")
        sys.exit(1)

if __name__ == "__main__":
    main()
