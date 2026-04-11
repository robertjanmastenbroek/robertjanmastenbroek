#!/usr/bin/env python3
"""
RJM Playlist Discovery Agent — CLI

Usage:
  python3 playlist_run.py status
  python3 playlist_run.py add <spotify_url> <relevance_1-10> <best_track> "<notes>"
  python3 playlist_run.py contact_found <spotify_id> <email_or_blank> "<contact_notes>"
  python3 playlist_run.py reject <spotify_id> "<reason>"
  python3 playlist_run.py pending_contact   # list verified playlists needing contact info
  python3 playlist_run.py list <status>     # discovered|verified|contact_found|contacted
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(__file__))
import playlist_db


def cmd_status():
    playlist_db.init_playlist_db()
    s = playlist_db.get_summary()
    print("\n=== RJM PLAYLIST DATABASE ===")
    print(f"  Total playlists:    {s.get('_total', 0)}")
    print(f"  Target:             200")
    print(f"  Progress:           {s.get('_total', 0)}/200 ({round(s.get('_total',0)/200*100)}%)")
    print()
    for status in ("discovered","verified","contact_found","contacted","responded","rejected"):
        n = s.get(status, 0)
        if n:
            print(f"  {status:<18} {n}")
    dist = s.get("_size_dist", {})
    if dist:
        print(f"\n  SIZE DISTRIBUTION (by followers):")
        print(f"  <1k followers:      {dist.get('tiny',0)}")
        print(f"  1k–5k followers:    {dist.get('small',0)}")
        print(f"  5k–20k followers:   {dist.get('medium',0)}")
        print(f"  >20k followers:     {dist.get('large',0)}")
    print()


def cmd_add(spotify_url, relevance, best_track, notes="", genre_tags="", source_query=""):
    playlist_db.init_playlist_db()
    # Extract Spotify ID from URL
    # https://open.spotify.com/playlist/37i9dQZF1DX... → 37i9dQZF1DX...
    spotify_id = spotify_url.rstrip("/").split("/")[-1].split("?")[0]
    ok, result = playlist_db.add_playlist(
        spotify_id=spotify_id,
        playlist_url=f"https://open.spotify.com/playlist/{spotify_id}",
        name="",   # will be filled by agent
        relevance_score=int(relevance),
        best_track_match=best_track,
        genre_tags=genre_tags,
        notes=notes,
        source_query=source_query,
    )
    if ok:
        print(f"✅ Added playlist: {spotify_id} (relevance={relevance}, track={best_track})")
    else:
        print(f"⚠️  Skipped: {result}")


def cmd_update(spotify_id, **kwargs):
    playlist_db.init_playlist_db()
    playlist_db.update_playlist(spotify_id, **kwargs)
    print(f"✅ Updated: {spotify_id}")


def cmd_contact_found(spotify_id, email, contact_notes=""):
    playlist_db.init_playlist_db()
    playlist_db.mark_contact_found(
        spotify_id,
        email=email if email and email != "-" else None,
        contact_notes=contact_notes,
    )
    print(f"✅ Contact found: {spotify_id} → {email or 'no email, see notes'}")


def cmd_reject(spotify_id, reason=""):
    playlist_db.init_playlist_db()
    playlist_db.update_playlist(spotify_id, status="rejected", notes=reason)
    print(f"✅ Rejected: {spotify_id} ({reason})")


def cmd_pending_contact():
    playlist_db.init_playlist_db()
    playlists = playlist_db.get_playlists_by_status("verified", limit=50)
    print(f"\n{len(playlists)} verified playlists needing contact info:\n")
    for p in playlists:
        print(f"  [{p['relevance_score']}/10] {p['name'] or p['spotify_id']}")
        print(f"         followers={p['follower_count']}  track={p['best_track_match']}")
        print(f"         curator={p['curator_name']}  {p['curator_spotify_url']}")
        print()


def cmd_list(status):
    playlist_db.init_playlist_db()
    playlists = playlist_db.get_playlists_by_status(status, limit=100)
    print(f"\n{len(playlists)} playlists with status='{status}':\n")
    for p in playlists:
        print(f"  [{p['relevance_score']}/10] {p['follower_count'] or '?'} followers — {p['name'] or p['spotify_id']}")
        print(f"         {p['playlist_url']}")
        if p.get("curator_email"):
            print(f"         email: {p['curator_email']}")
        print()


def main():
    args = sys.argv[1:]
    if not args or args[0] == "status":
        cmd_status()
    elif args[0] == "add" and len(args) >= 3:
        notes     = args[3] if len(args) > 3 else ""
        genre_tags = args[4] if len(args) > 4 else ""
        cmd_add(args[1], args[2], args[2], notes, genre_tags)
    elif args[0] == "contact_found" and len(args) >= 3:
        notes = args[3] if len(args) > 3 else ""
        cmd_contact_found(args[1], args[2], notes)
    elif args[0] == "reject" and len(args) >= 2:
        reason = args[2] if len(args) > 2 else ""
        cmd_reject(args[1], reason)
    elif args[0] == "pending_contact":
        cmd_pending_contact()
    elif args[0] == "list" and len(args) >= 2:
        cmd_list(args[1])
    elif args[0] == "update" and len(args) >= 3:
        # update <spotify_id> <field>=<value> ...
        fields = {}
        for kv in args[2:]:
            if "=" in kv:
                k, v = kv.split("=", 1)
                fields[k] = v
        cmd_update(args[1], **fields)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
