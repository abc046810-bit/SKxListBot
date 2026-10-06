# SKxListBot

Simple list bot: poster + title + screenshot **links** → private preview → channel post + ❤️ votes.

**No MongoDB.** Votes live in memory (fine for a few hours while bot is online via UptimeRobot).

## Username ideas (BotFather)
Pick one that is free:
- `@SKxListBot` — clear
- `@SKxDemandBot` — demand / votes
- `@SKListVoteBot`
- `@SKxWantedListBot`

## Votes + forward
Votes work on the **original message the bot posts** in the channel.

If you **forward** that post to another channel:
- Forward often **does not keep live vote buttons**
- Counts will **not** update on the forward

**Better:** run `/setchannel` on the channel where you want votes, and let the bot post there.  
Or post once in main channel and share a **link** to that post (not forward as copy).

## Render env
```
BOT_TOKEN=...
OWNER_ID=8723278238
PORT=8080
```

Start: `python bot.py`  
UptimeRobot → your Render URL every 5 min.

Bot must be **admin** in the target channel (`/setchannel @Channel`).

## Usage
1. `/setchannel @YourChannel`
2. `/new`
3. Type → poster URL → title → screenshot URLs → Done
4. Add more (max 10) or Finish
5. Preview → Post to channel
6. Users tap ❤️ 1, ❤️ 2, …
