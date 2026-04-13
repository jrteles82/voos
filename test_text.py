import db as sqlite3
db = "/home/teles/dev/python/skyscanner-bot/flight_tracker_browser.db"
conn = sqlite3.connect(db)
c = conn.cursor()
c.execute("SELECT raw_text, best_vendor FROM results WHERE site='google_flights' AND raw_text IS NOT NULL AND raw_text != '' ORDER BY id DESC LIMIT 5")
for r in c.fetchall():
    print("VENDOR:", r[1])
    print("TEXT:", repr(r[0][:200]))
    print("---")