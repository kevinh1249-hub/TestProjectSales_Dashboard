#!/usr/bin/env python3
"""
Call Coach — analyzes a Fireflies transcript and updates coaching.json

Usage:
    python3 analyze_call.py <call_id> <rep_id>

Example:
    python3 analyze_call.py 01KTC12KTD1X6JPR4ARXNDTFZ4 jordan
"""

import os
import sys
import json
import requests
from datetime import datetime
import anthropic

FIREFLIES_KEY  = os.environ.get('FIREFLIES_API_KEY')
ANTHROPIC_KEY  = os.environ.get('ANTHROPIC_API_KEY')
FIREFLIES_URL  = 'https://api.fireflies.ai/graphql'
DATA_FILE      = os.path.join(os.path.dirname(__file__), 'data', 'coaching.json')
MODEL          = 'claude-haiku-4-5-20251001'

DIM_LABELS = {
    'listening_ratio':    'Listening Ratio',
    'problem_surfacing':  'Problem Surfacing',
    'credibility_moments':'Credibility Moments',
    'next_step_clarity':  'Next Step Clarity',
    'curiosity_unlocked': 'Curiosity Unlocked',
}

RUBRIC_PROMPT = """You are a sales call coach. Analyze this transcript and score it on 5 dimensions.

CALL TITLE: {title}

TRANSCRIPT:
{transcript}

Score each dimension 1-5:

1. LISTENING RATIO — Were they asking questions and drawing the other person out, or pitching?
   5=Led with question, mostly listening | 3=Even split | 1=Dominated, rarely let other talk

2. PROBLEM SURFACING — Did a specific pain or opportunity emerge, or did it stay generic?
   5=Sharp specific problem identified and explored | 3=Some specificity | 1=No specific problem surfaced

3. CREDIBILITY MOMENTS — Did they share concrete examples from their own experience?
   5=Multiple specific examples, felt like a peer | 3=Mentioned experience but vague | 1=No credibility moments

4. NEXT STEP CLARITY — Did the call end with something concrete?
   5=Clear next step, specific and time-bound | 3=Vague intention to follow up | 1=Call ended with nothing

5. CURIOSITY UNLOCKED — Did the other person ask questions or lean in?
   5=They asked multiple questions, offered to help | 3=Engaged but did not lean in actively | 1=No signs of curiosity

For scores 1-3: give coaching feedback with a specific quote and a concrete thing to practice.
For scores 4-5: give positive feedback with a specific quote and what worked.

Return ONLY valid JSON in exactly this format, no other text:
{{
  "overall_score": <average of all 5 scores to 1 decimal>,
  "dimensions": {{
    "listening_ratio":     {{"score": <1-5>, "quote": "<exact quote from transcript>", "feedback": "<2-3 sentences>"}},
    "problem_surfacing":   {{"score": <1-5>, "quote": "<exact quote from transcript>", "feedback": "<2-3 sentences>"}},
    "credibility_moments": {{"score": <1-5>, "quote": "<exact quote from transcript>", "feedback": "<2-3 sentences>"}},
    "next_step_clarity":   {{"score": <1-5>, "quote": "<exact quote from transcript>", "feedback": "<2-3 sentences>"}},
    "curiosity_unlocked":  {{"score": <1-5>, "quote": "<exact quote from transcript>", "feedback": "<2-3 sentences>"}}
  }},
  "manager_bullets": [
    "<high-level observation a manager needs to know>",
    "<second observation>",
    "<third observation>"
  ],
  "flag_dimension": "<snake_case name of lowest-scoring dimension>",
  "flag_score": <score of that dimension>
}}"""


def get_transcript(call_id):
    query = '{ transcript(id: "%s") { id title date sentences { text speaker_name } } }' % call_id
    r = requests.post(
        FIREFLIES_URL,
        json={'query': query},
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {FIREFLIES_KEY}'}
    )
    r.raise_for_status()
    return r.json()['data']['transcript']


def format_transcript(transcript):
    return '\n'.join(f"{s['speaker_name']}: {s['text']}" for s in transcript['sentences'])


def analyze(transcript_text, title):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    prompt = RUBRIC_PROMPT.format(title=title, transcript=transcript_text)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{'role': 'user', 'content': prompt}]
    )
    return json.loads(msg.content[0].text)


def update_coaching_json(rep_id, call_id, title, date_str, ff_url, analysis):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)

    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            data = json.load(f)
    else:
        data = {}

    if rep_id not in data:
        data[rep_id] = {'calls': []}

    rep = data[rep_id]

    # Remove duplicate if re-running same call
    rep['calls'] = [c for c in rep['calls'] if c['id'] != call_id]

    # Prepend newest first
    rep['calls'].insert(0, {
        'id': call_id,
        'title': title,
        'date': date_str,
        'fireflies_url': ff_url,
        'overall_score': analysis['overall_score'],
        'dimensions': analysis['dimensions']
    })

    scores = [c['overall_score'] for c in rep['calls']]
    rep['avg_score']      = round(sum(scores) / len(scores), 1)
    rep['calls_analyzed'] = len(rep['calls'])

    if len(rep['calls']) >= 2:
        diff = rep['calls'][0]['overall_score'] - rep['calls'][1]['overall_score']
        if diff >= 0.3:
            rep['trend'], rep['trend_direction'] = 'improving', 'up'
        elif diff <= -0.3:
            rep['trend'], rep['trend_direction'] = 'declining', 'down'
        else:
            rep['trend'], rep['trend_direction'] = 'steady', 'flat'
    else:
        rep['trend'], rep['trend_direction'] = 'insufficient_data', 'flat'

    # Consistent themes across all calls
    dim_avgs = {}
    for call in rep['calls']:
        for dim, d in call['dimensions'].items():
            dim_avgs.setdefault(dim, []).append(d['score'])

    rep['consistent_strengths']    = [DIM_LABELS[d] for d, s in dim_avgs.items() if sum(s)/len(s) >= 4]
    rep['consistent_improvements'] = [DIM_LABELS[d] for d, s in dim_avgs.items() if sum(s)/len(s) <= 2.5]

    rep['manager_bullets'] = analysis['manager_bullets']
    rep['flag_call'] = {
        'dimension':    analysis['flag_dimension'],
        'score':        analysis['flag_score'],
        'fireflies_url': ff_url,
        'call_title':   title
    }

    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"  Avg score: {rep['avg_score']}/5 across {rep['calls_analyzed']} call(s)")
    print(f"  Trend: {rep['trend']}")


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 analyze_call.py <call_id> <rep_id>")
        print("Example: python3 analyze_call.py 01KTC12KTD1X6JPR4ARXNDTFZ4 jordan")
        sys.exit(1)

    if not FIREFLIES_KEY or not ANTHROPIC_KEY:
        print("Error: FIREFLIES_API_KEY and ANTHROPIC_API_KEY must be set in your environment.")
        print("Run: source ~/.zprofile")
        sys.exit(1)

    call_id, rep_id = sys.argv[1], sys.argv[2]

    print(f"Fetching transcript {call_id}...")
    t = get_transcript(call_id)

    title    = t['title']
    date_str = datetime.fromtimestamp(t['date'] / 1000).strftime('%Y-%m-%d')
    ff_url   = f"https://app.fireflies.ai/view/{title.replace(' ', '-')}::{call_id}"

    print(f"Got: {title} ({date_str})")
    print(f"Analyzing with Claude...")

    analysis = analyze(format_transcript(t), title)

    print(f"Score: {analysis['overall_score']}/5")
    print(f"Updating coaching.json...")

    update_coaching_json(rep_id, call_id, title, date_str, ff_url, analysis)
    print("Done. Push to GitHub to update the live dashboard.")


if __name__ == '__main__':
    main()
