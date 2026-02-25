# Prompt Test Results

Dynamic examples: enabled
Model: deepseek-chat


## Positive Reply (Rapport Building)


### Test 1: Joyce PBIS
**Stage:** positive_reply | This is the lead's first substantive reply to our initial outreach, showing engagement and sharing information about their own project while providing feedback about our profile.
**First reply:** True

**Lead's message:**
> Hi Ian. Love that your an engineer with a passion for family and solutions. 
I’m bootstrapping iquotehero.com PaaS engine 

Joyce. 
Ps. it’s a super challenge to read your about section. May I suggest white space as an asset ? 

**What was actually sent (approved):**
```
Thanks Joyce. Appreciate the feedback on the profile - I see what you mean re-reading it actually

iQuote looks interesting by the way. Tracking trust building actions - that's neat

So key in differentiating in the post AI world

When you say bootstrapping - does that mean no active users at the minute?
```

**What AI generates NOW:**
```
Appreciate the feedback on the profile - I see what you mean re-reading it actually

iQuoteHero looks interesting btw. A PaaS engine for what exactly?

When you say bootstrapping - does that mean no active users at the minute?
```


### Test 2: Patrick Hennessy
**Stage:** positive_reply | This is the lead's second substantive reply in the conversation, responding to our question about their client acquisition channels with a comment about the file format, indicating continued engagement but no pitch for a call has been made yet.
**First reply:** True

**Lead's message:**
> the file came out looking like ancient Egyptian

**What was actually sent (approved):**
```
The activity score is key as it reflects prospects who are more likely to reply. Where it gets juicy is where we have a fuller picture of the pain points of your icp. Then we can find those saying directly they have those. Dr Amy P is one of those

I included the linkedin profiles for you. Is LinkedIn a main driver for clients for you? Or more word of mouth/warm outreach?
```

**What AI generates NOW:**
```
Haha sorry about that - let me fix it

Is it opening in a weird format? Or just garbled text?
```


### Test 3: Soumyadeep Mondal
**Stage:** positive_reply | The lead has replied to our outreach with 'Cool', which is a substantive response indicating engagement and interest, but we haven't yet pitched a call or meeting in the conversation history.
**First reply:** False

**Lead's message:**
> Cool

**What was actually sent (approved):**
```
Sounds good. 

https://calendly.com/scalingsmiths/discoverycall

Should have space still Fri/Sat 
```

**What AI generates NOW:**
```
Of course

Hmm - how's it going on here for you?  
Good # of clients? Or nah
```


## Pitched (Call Invitation)


### Test 1: Patrick J. Ryan
**Stage:** positive_reply | This is the lead's first substantive reply to our outreach where they're sharing a specific business challenge (bottleneck between outreach and booking calls), which indicates interest and qualifies as a positive engagement before we've pitched a call.
**First reply:** True

**Lead's message:**
> I have a bottleneck between outreach and booking calls.

**What was actually sent (approved):**
```
Hey Patrick appreciate the hand written note

Did you go through Cole and Dickie's ghostwriting course? I did ship30 like 13/14months ago

Dig the personal side of your posts

Solid focus on execs. Is it specifically EEC rather than content you do? How's outreach going on here? Is this the main client acq channel for you
```

**What AI generates NOW:**
```
Hmm, that's a common but tough spot to be in.

Is the outreach volume not high enough, or are replies just not converting?

And is this for your own client acquisition, or for your executive coach clients?
```


### Test 2: Catherine Long, MHRD, PROSCI® Certified Change Practitioner
**Stage:** positive_reply | This is the lead's first substantive reply to our outreach where they're engaging with our offer, showing interest but with a timing objection. They haven't been pitched a call yet, and we're still in the initial qualification/rapport-building phase.
**First reply:** True

**Lead's message:**
> Ian, I'm so slammed the next 2 weeks, I'd have to push that idea down the road unfortunately....  I so appreaciate your offer.

**What was actually sent (approved):**
```
Of course Catherine - you welcome. Like how you connect to business outcomes in your headline btw. The closer to the money you can be - the easier it is to ask for it hahaha

7 figure revenue shift is a strong case study for sure

Is it the founders/ceos that you reach out to?
```

**What AI generates NOW:**
```
Of course - no worries at all

Totally get being slammed

When things clear up, curious - how do you typically connect with those CEOs? More through your network or outreach here?
```


### Test 3: Bernard Baah — ERROR: 'charmap' codec can't encode character '\u2192' in position 4: character maps to <undefined>


## Calendar Sent


### Test 1: Vikas Pandey
**Stage:** calendar_sent | The lead explicitly requested a calendar link ('Send me your calendar, I will pick up a time.'), and we responded by providing a Calendly booking link, which matches the calendar_sent stage definition.
**First reply:** False

**Lead's message:**
> Send me your calendar, I will pick up a time.

**What was actually sent (approved):**
```
Sure

Book here

https://calendly.com/scalingsmiths/discoverycall
```

**What AI generates NOW:**
```
Sure

Book here

https://calendly.com/scalingsmiths/discoverycall
```


### Test 2: Andy Bergmann
**Stage:** calendar_sent | The lead agreed to meet ('Yeah I'd be interested in that!'), we responded by sending a Calendly link, and now the lead is responding to that calendar link with scheduling questions about availability.
**First reply:** False

**Lead's message:**
> Thanks for shooting that over. Looks like there is only times available this week. I'm OOO this entire week. Got any time the following week?

**What was actually sent (approved):**
```
Understood. Tell you what - book a time for this wk and I'll move it 7 days
```

**What AI generates NOW:**
```
Book a slot for this week and I’ll shift it to next week for you
```


### Test 3: Andy Bergmann
**Stage:** calendar_sent | The lead agreed to meet ('Yeah I'd be interested in that!'), and we responded by sending a Calendly link, which constitutes sending a calendar/booking link.
**First reply:** False

**Lead's message:**
> Thanks for shooting that over. Looks like there is only times available this week. I'm OOO this entire week. Got any time the following week?

**What was actually sent (approved):**
```
Sure let's do it

Here

https://calendly.com/scalingsmiths/discoverycall

Should have some free time early next wk 
```

**What AI generates NOW:**
```
Cool. Book any slot this week and I'll shift it to next week for you
```


## Booked


### Test 1: Doug Huete
**Stage:** pitched | The user has just invited the lead to schedule a call ('Are you free some time Mon/Tue?') in their most recent message, which is a clear pitch for a meeting, and the lead's latest message shows interest in discussing further but hasn't yet agreed to the specific scheduling request.
**First reply:** False

**Lead's message:**
> i’m open to a discussion. It’s just that today was not a good day.
And I would be looking to enhance our clients’ outreach with a system that has proven performance metrics

Anything that performs well under scrutinyshould be viewed as an asset. 

The question is
How long does it take to get a return on investment?



**What was actually sent (approved):**
```
Understood

Like to hear it's a proven system. Would be interested to dive deeper into that

Our kpi is 4+ calls/wk. We've sold deal sizes ranging up to $25k on linkedIn

ROI is dependent on quite a few things - not least their icp and deal size

Would need to dive deeper on the call 

Are you free some time Mon/Tue?
```

**What AI generates NOW:**
```
No worries at all

Our focus is on proven performance, not just the tech behind it

ROI timeline really depends on your clients' deal size and ICP

Easiest to unpack that live. Friday works for me

Here's the link to grab a slot: https://calendly.com/scalingsmiths/discoverycall
```


### Test 2: Doug Huete
**Stage:** regeneration | The conversation had stalled after the lead expressed interest in discussing but didn't book a meeting, and our latest message on Feb 19 is a follow-up after a significant time gap (13 days) to re-engage the lead who previously showed interest but didn't schedule.
**First reply:** False

**Lead's message:**
> i’m open to a discussion. It’s just that today was not a good day.
And I would be looking to enhance our clients’ outreach with a system that has proven performance metrics

Anything that performs well under scrutinyshould be viewed as an asset. 

The question is
How long does it take to get a return on investment?



**What was actually sent (approved):**
```
Appreciate the energy Doug

People don't have an issue with an AI msg. They have issues with messages that are not relevant to them

Finding prospects showing signals of pain points just improves results 10 fold

Check this out https://docs.google.com/document/d/1l_vqZv-MRDK1RNV66gVucxKW5_pd-JjWZRYAzyOMPmI/edit?tab=t.0

How we added $25k/mo for a client through LinkedIn - targeting CEOs

How do you find small businesses for GTML? Is that warm network/referalls? Or on here
```

**What AI generates NOW:**
```
Hey Doug - just had a client hit 6 booked calls this week using a simple trigger-based system

Made me think of your question about ROI timelines

How's the outreach system performing for your clients lately?
```


## Re-engagement

*  No approved conversations found for stage: regeneration*


---
**Summary:** 10 tests across 4 stages