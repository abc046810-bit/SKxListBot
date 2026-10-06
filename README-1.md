# SKxListBot

Channel **list + expandable screenshots + ❤️ votes** in **one Rich Message post**.

No MongoDB. Votes in memory (few hours while bot is online).

---

## Post layout (what users see)

```
📋 TODAY'S LIST

1. 🎬 Title
   Poster · Trailer
   📸 SCREENSHOTS (N) — Tap to open & swipe
   → expand · swipe all shots (same post)

2. 📺 Next title
   …
   
❤️ 1   ❤️ 2   ❤️ 3
```

Screenshots are **inside the same post** (not separate albums), like SKxPoster style.

---

## Admin flow

1. `/new`
2. Type → **poster URL** → title → trailer URL (optional)
3. Send screenshot **photos** (upload/forward, max 10 per item) → Done
4. Add more items (max 10) or Finish
5. Private preview → Post to channel

---

## Setup (Render)

| Env | Value |
|-----|--------|
| `BOT_TOKEN` | BotFather token |
| `OWNER_ID` | `8723278238` |
| `PORT` | `8080` |

```bash
python bot.py
```

UptimeRobot → your Render URL every 5 min.

Bot must be **Admin** in channel → `/setchannel @Channel`

---

## Commands

`/new` `/setchannel` `/getchannel` `/cancel` `/help` `/addadmin` `/admins`

---

## Notes

- Votes on **original** list message only (forwards unreliable)
- Rich Message required for expandable swipe screenshots
- Fallback = text list if Rich Message fails
- Max 10 items · max 10 screenshots each
