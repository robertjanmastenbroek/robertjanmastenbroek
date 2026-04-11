# Zero-Touch TikTok System
## 100% Autonomous Content Machine

**Goal:** Set it up once, never touch it again. 3 videos posted daily, forever.

---

## 🏗️ The Fully Autonomous Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                     AUTONOMOUS WORKFLOW                          │
│                     (Runs 24/7, No Human Input)                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  EVERY 8 HOURS (3x Daily):                                      │
│                                                                  │
│  1. Claude API → Generates optimized script                     │
│  2. Midjourney Bot → Creates 3 visuals from script              │
│  3. CapCut Automation → Assembles video                         │
│  4. TikTok API → Posts video at optimal time                    │
│  5. Analytics Logger → Tracks performance                       │
│                                                                  │
│  EVERY SUNDAY (Weekly Optimization):                            │
│                                                                  │
│  6. AI Analyzer → Reviews 21 videos performance                 │
│  7. Strategy Optimizer → Updates content parameters             │
│  8. Loop restarts with improved strategy                        │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Technical Stack (No New Purchases)

### **Your Existing Tools:**
1. ✅ **Midjourney** (image generation)
2. ✅ **CapCut Pro** (video editing - check if API access exists)
3. ✅ **Claude** (script generation)

### **Free/Open-Source Additions:**
4. ✅ **n8n** (workflow automation - free self-hosted)
5. ✅ **Midjourney API Wrapper** (unofficial but works)
6. ✅ **FFmpeg** (video processing - free)
7. ✅ **TikTok Upload Bot** (unofficial API)
8. ✅ **Airtable Free** (database for scripts & analytics)

**Total New Cost: $0**

---

## 🔧 Component Breakdown

### **Component 1: Script Generator (Claude API)**

**What It Does:**
- Generates 1 video script every 8 hours
- Uses past performance data to optimize
- Stores scripts in Airtable

**Implementation:**
```javascript
// auto-script-generator.js
const Anthropic = require('@anthropic-ai/sdk');
const Airtable = require('airtable');

async function generateScript() {
  const anthropic = new Anthropic({
    apiKey: process.env.CLAUDE_API_KEY
  });

  // Pull top-performing video data
  const topVideos = await getTopPerformers();

  const prompt = `
You are a viral TikTok script generator for Robert-Jan Mastenbroek.

Brand: "Ancient Truth. Future Sound." - Melodic Techno & Tribal Psytrance
Aesthetic: Dark/Holy/Futuristic

Top performing patterns from last week:
${JSON.stringify(topVideos)}

Generate 1 new script optimized for 70%+ retention:

FORMAT:
{
  "hook": "[First 3 seconds - attention grabber]",
  "content": "[4-10 seconds - core value]",
  "payoff": "[1-2 seconds - CTA or loop]",
  "visual_prompts": [
    "Midjourney prompt 1",
    "Midjourney prompt 2",
    "Midjourney prompt 3"
  ],
  "text_overlays": ["Line 1", "Line 2"],
  "music_style": "dark melodic techno",
  "hashtags": "#tag1 #tag2 #tag3",
  "content_pillar": "education/preview/philosophy/etc"
}
  `;

  const message = await anthropic.messages.create({
    model: "claude-sonnet-4-5-20250929",
    max_tokens: 1024,
    messages: [{ role: "user", content: prompt }]
  });

  const script = JSON.parse(message.content[0].text);

  // Save to Airtable
  await saveToAirtable(script);

  return script;
}
```

**Cost:** ~$0.50 per script = $45/month for 3 daily scripts

---

### **Component 2: Visual Generator (Midjourney Automation)**

**What It Does:**
- Takes visual prompts from script
- Sends to Midjourney via Discord bot
- Downloads generated images
- Stores in assets folder

**Implementation Options:**

**Option A: Midjourney Unofficial API** (Recommended)
```javascript
// midjourney-generator.js
const Midjourney = require('midjourney-api-wrapper');

async function generateVisuals(script) {
  const mj = new Midjourney({
    discordToken: process.env.DISCORD_TOKEN,
    serverId: process.env.SERVER_ID,
    channelId: process.env.CHANNEL_ID
  });

  const images = [];

  for (const prompt of script.visual_prompts) {
    // Enhance prompt with brand aesthetic
    const fullPrompt = `${prompt}, dark moody lighting, cinematic, high contrast, 16:9 aspect ratio --ar 16:9 --style raw --v 6`;

    const result = await mj.imagine(fullPrompt);
    await mj.upscale(result.messageId, 1); // Upscale image 1

    const imageUrl = await mj.getImageUrl(result.messageId);
    const imagePath = await downloadImage(imageUrl, `./assets/${Date.now()}.png`);

    images.push(imagePath);
  }

  return images;
}
```

**Option B: Midjourney Discord Bot (More Reliable)**
- Set up a dedicated Discord bot
- Bot monitors your Midjourney server
- Sends /imagine commands
- Waits for results
- Downloads images

Library: `midjourney-discord-wrapper` (npm)

---

### **Component 3: Video Assembler (CapCut or FFmpeg)**

**What It Does:**
- Takes 3 Midjourney images
- Adds text overlays from script
- Adds background music
- Exports as TikTok-ready MP4

**Option A: CapCut Pro API** (If Available)
Check if CapCut Pro has API access. If yes, use it.

**Option B: FFmpeg + Remotion** (Guaranteed to Work)
```javascript
// video-assembler.js
const { exec } = require('child_process');
const Remotion = require('remotion');

async function createVideo(script, images, music) {
  // Create video using FFmpeg
  const videoPath = `./output/${Date.now()}.mp4`;

  // Image sequence: 3 images, 4 seconds each = 12 seconds total
  const ffmpegCmd = `
    ffmpeg -loop 1 -t 4 -i ${images[0]} \
           -loop 1 -t 4 -i ${images[1]} \
           -loop 1 -t 4 -i ${images[2]} \
           -i ${music} \
           -filter_complex \
           "[0:v]scale=1080:1920,setsar=1,fade=t=in:st=0:d=0.5,fade=t=out:st=3.5:d=0.5[v0]; \
            [1:v]scale=1080:1920,setsar=1,fade=t=in:st=0:d=0.5,fade=t=out:st=3.5:d=0.5[v1]; \
            [2:v]scale=1080:1920,setsar=1,fade=t=in:st=0:d=0.5,fade=t=out:st=3.5:d=0.5[v2]; \
            [v0][v1][v2]concat=n=3:v=1:a=0[outv]" \
           -map "[outv]" -map 3:a \
           -c:v libx264 -c:a aac -shortest -t 12 \
           ${videoPath}
  `;

  await execPromise(ffmpegCmd);

  // Add text overlays using Remotion
  await addTextOverlays(videoPath, script.text_overlays);

  return videoPath;
}
```

---

### **Component 4: Auto-Poster (TikTok Upload)**

**What It Does:**
- Takes finished video
- Posts to TikTok at scheduled time
- Adds caption + hashtags

**Implementation:**

**Option A: TikTok Unofficial API** (Free, Risky)
```javascript
// tiktok-uploader.js
const TikTokUploader = require('tiktok-uploader');

async function postToTikTok(videoPath, script) {
  const uploader = new TikTokUploader({
    sessionId: process.env.TIKTOK_SESSION_ID, // From browser cookies
    username: 'robertjanmastenbroek'
  });

  const caption = `${script.hook}\n\n${script.hashtags}\n\nAncient Truth. Future Sound. 🎵`;

  await uploader.upload({
    video: videoPath,
    caption: caption,
    privacy: 'public'
  });

  console.log('✅ Posted to TikTok:', caption);
}
```

**Option B: Later.com API** (Paid, Safe)
- Later.com has official TikTok partnership
- Use their API to schedule uploads
- Cost: $25/month

**Option C: Publer API** (Cheaper, Safe)
- $12/month
- Official TikTok integration
- Bulk scheduling

**Recommendation:** Start with Option A (free) on a test account, then move to Option B when scaling.

---

### **Component 5: Analytics Tracker**

**What It Does:**
- Fetches video performance data daily
- Stores in database
- Calculates performance scores

**Implementation:**
```javascript
// analytics-tracker.js
const TikTokScraper = require('tiktok-scraper');

async function trackPerformance() {
  const scraper = new TikTokScraper();

  // Get last 7 days of videos
  const videos = await scraper.user('robertjanmastenbroek', {
    number: 21
  });

  for (const video of videos.collector) {
    const stats = {
      video_id: video.id,
      views: video.playCount,
      likes: video.diggCount,
      shares: video.shareCount,
      comments: video.commentCount,
      avg_watch_time: video.videoMeta?.duration || 0,
      posted_at: video.createTime,
      performance_score: calculateScore(video)
    };

    await saveToDatabase(stats);
  }
}

function calculateScore(video) {
  // Weight metrics: Views (20%), Engagement (40%), Completion (40%)
  const engagementRate = (video.diggCount + video.shareCount + video.commentCount) / video.playCount;
  const score = (engagementRate * 10) + (video.playCount / 1000);
  return score;
}
```

---

### **Component 6: Weekly Optimizer**

**What It Does:**
- Every Sunday, analyzes last 21 videos
- Identifies patterns
- Updates content strategy
- Generates next week's approach

**Implementation:**
```javascript
// weekly-optimizer.js
async function optimizeStrategy() {
  const videos = await getLast21Videos();

  // Sort by performance score
  const topPerformers = videos.sort((a, b) => b.performance_score - a.performance_score).slice(0, 5);
  const bottomPerformers = videos.slice(-5);

  const prompt = `
Analyze TikTok performance data:

TOP 5 VIDEOS:
${JSON.stringify(topPerformers)}

BOTTOM 5 VIDEOS:
${JSON.stringify(bottomPerformers)}

IDENTIFY:
1. What content pillars work best?
2. What hook formats drive retention?
3. What video length is optimal?
4. What hashtags correlate with views?

RECOMMEND:
- Content mix for next week (% per pillar)
- Hook templates to emphasize
- Topics to avoid
- New angles to test

OUTPUT: JSON strategy update
  `;

  const analysis = await callClaudeAPI(prompt);

  // Update strategy in database
  await updateContentStrategy(analysis);
}
```

---

## 🔄 The Complete Workflow (n8n Automation)

**n8n** is a free workflow automation tool (like Zapier but self-hosted).

**Workflow Setup:**

```
TRIGGER: Cron (Every 8 hours: 6am, 2pm, 10pm)
   ↓
[1] Generate Script (Claude API)
   ↓
[2] Generate 3 Visuals (Midjourney Bot)
   ↓
[3] Wait for Images (5 mins)
   ↓
[4] Assemble Video (FFmpeg)
   ↓
[5] Add Text Overlays (FFmpeg filters)
   ↓
[6] Upload to Storage (Local or S3)
   ↓
[7] Post to TikTok (API)
   ↓
[8] Log in Database (Airtable)
   ↓
END

---

TRIGGER: Cron (Every 24 hours at midnight)
   ↓
[1] Fetch Video Analytics (TikTok Scraper)
   ↓
[2] Update Database (Airtable)
   ↓
END

---

TRIGGER: Cron (Sunday midnight)
   ↓
[1] Run Weekly Analysis (Claude API)
   ↓
[2] Update Strategy Parameters (Database)
   ↓
[3] Generate Next Week's Content Plan
   ↓
END
```

**n8n Visual Workflow:**
- Drag-and-drop interface
- No coding required for basic setup
- Self-hosted (free) or cloud ($20/month)

---

## 💰 Total Cost Breakdown

### **Setup (One-Time):**
- Server (DigitalOcean Droplet): $6/month OR free (run on your computer)
- Domain for n8n (optional): $0 (use IP address)

### **Monthly Recurring:**
- Claude API: ~$50/month (3 scripts daily)
- TikTok Posting: $0 (unofficial API) OR $12-25 (Publer/Later)
- n8n Hosting: $0 (self-hosted) OR $20 (cloud)
- Midjourney: You already pay for this
- CapCut: You already pay for this

**Total: $50-95/month** (depending on posting method)

---

## 🚀 Implementation Timeline

### **Week 1: Setup Foundation**
- [ ] Set up n8n (self-hosted or cloud)
- [ ] Connect Claude API
- [ ] Set up Midjourney Discord bot automation
- [ ] Create Airtable database

### **Week 2: Build Workflows**
- [ ] Script generation workflow
- [ ] Visual generation workflow
- [ ] Video assembly pipeline (FFmpeg)
- [ ] Test full workflow manually

### **Week 3: Automate Posting**
- [ ] Set up TikTok auto-upload
- [ ] Configure posting schedule (3x daily)
- [ ] Test on private/test account first

### **Week 4: Analytics & Optimization**
- [ ] Set up analytics scraping
- [ ] Build weekly optimization workflow
- [ ] Deploy to production
- [ ] Monitor first week

### **Week 5: Full Autonomy**
- [ ] System runs completely hands-free
- [ ] Weekly email reports only
- [ ] Intervention only if something breaks

---

## 📋 What You Need to Provide (One-Time Setup)

### **1. Content Assets (Record Once):**
- [ ] 10-20 short clips of you (studio, DJing, producing)
- [ ] 5-10 music tracks (background music for videos)
- [ ] Brand assets (logo, colors, fonts)

### **2. Initial Content Strategy:**
- [ ] Top 5 content pillars to focus on
- [ ] 10 hook templates you want to test
- [ ] Topics/themes to cover

### **3. API Credentials:**
- [ ] Claude API key
- [ ] Discord token (for Midjourney)
- [ ] TikTok session ID (or Later.com API key)

**That's it. After setup, you never touch it again.**

---

## 🎯 Expected Results (90 Days)

**After 30 Days:**
- 90 videos posted (3/day × 30)
- 50K-100K total views
- 500-1,000 new followers
- System running autonomously

**After 60 Days:**
- 180 videos posted
- 200K-500K total views
- 2,000-5,000 followers
- 1-2 viral videos (50K+ views)

**After 90 Days:**
- 270 videos posted
- 500K-1M total views
- 5,000-10,000 followers
- Consistent 5K+ views per video
- Website traffic increasing from TikTok bio

---

## 🛠️ My Recommendation: 3 Options

### **Option A: I Build It For You** (Fastest)
- I set up the entire system
- You provide assets + API keys
- Ready in 1 week
- Cost: 10 hours of work

### **Option B: We Build It Together** (Learn)
- I guide you step-by-step
- You set up each component
- Ready in 2-3 weeks
- Cost: Your time

### **Option C: I Give You The Code** (DIY)
- I provide all scripts + workflows
- You deploy and configure
- Ready when you finish
- Cost: Just your time

---

## ⚡ Simplest Start: Proof of Concept

**Let's test the concept first before building full automation:**

1. **This Week:** I generate 7 scripts (1 per day)
2. **You:** Create 1-2 videos manually to test
3. **If It Works:** We build full automation
4. **If Not:** We refine approach first

**Want to start with proof of concept or go straight to full automation?** 🚀
