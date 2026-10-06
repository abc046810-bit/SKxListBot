# SKxListBot

Channel **list + ❤️ votes + screenshot albums**.

No MongoDB. Votes in memory (fine for a few hours with UptimeRobot).

---

## What it does

1. Admin builds a list (1–10 items): Movie / Web Series / Show  
2. Each item: **poster URL** → **title** → **trailer URL** (optional) → **screenshot photos**  
3. Private full preview (list + albums)  
4. Post to channel:
   - Stylish list (Rich Message when available)
   - ❤️ vote buttons  
   - **Screenshot albums** under the list (swipe, like other bots)

---

## Screenshots (important)

- Send **photos** (upload / forward images)  
- **Not** screenshot links  
- Max **10 photos** per item (Telegram album limit)  
- Each item → its own album under the main list post  

---

## Votes

- Work on the **original** list message the bot posts  
- **Forwarded** copies usually do **not** keep live votes  
- Prefer: bot posts in the channel where you want votes  

---

## Setup (Render free)

### Env
| Key | Value |
|-----|--------|
| `BOT_TOKEN` | from @BotFather |
| `OWNER_ID` | `8723278238` |
| `PORT` | `8080` |

### Start
```bash
python bot.py
```

### Keep awake
UptimeRobot → HTTP monitor on your Render URL every 5 minutes.

### Channel
1. Add bot as **Admin** in the channel  
2. `/setchannel @YourChannel`  

---

## Commands

| Command | Use |
|---------|-----|
| `/new` | Create list |
| `/setchannel` | Set post channel |
| `/getchannel` | Show channel |
| `/cancel` | Cancel current flow |
| `/help` | Help |
| `/addadmin` | Owner: add admin |
| `/admins` | List admins |

---

## Flow

```
/new
→ Type
→ Poster URL
→ Title
→ Trailer URL (or Skip)
→ Screenshot PHOTOS → Done
→ Add another / Finish
→ Private preview
→ Post to channel
```

---

## Username ideas

- `@SKxListBot`
- `@SKxDemandBot`
- `@SKListVoteBot`

---

## Notes

- Max **10 items** per list  
- Votes reset if Render process restarts (no DB by design)  
- Best for short windows (3–6 hours), then delete the channel post yourself  
