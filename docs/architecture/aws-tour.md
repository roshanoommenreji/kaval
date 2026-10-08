# AWS, plain and simple

> Written for someone who has never touched AWS before. If you already know what a VPC or an IAM role is, the [System topology](#system) diagram is the faster, denser version of this same information.

## The flow, in one picture

Tap any box for exactly what it does, what it's made of, and what it's connected to.

<!-- diagram -->

## What AWS even is

Normally, if you want a computer that's always on and reachable from anywhere, you'd have to buy one, plug it in, and make sure it never loses power or breaks. That's expensive and a lot of work.

AWS (Amazon Web Services) is Amazon renting out pieces of its own giant computer warehouses to anyone who wants them. You don't get a physical box shipped to you — you ask for a "computer" or a "locked cupboard" or a "guard" over the internet, AWS gives you one inside one of its buildings, and you only pay for the bit you actually used, by the hour or by the gigabyte. Stop using it, stop paying.

Everything below is one of those pieces. None of it is pretend or "coming later" — this is the actual list of things switched on for Kaval right now, and what each one is doing for us.

## The money guard

Before anything else was built, a guard was hired to watch the spending, because the easiest way to ruin a hobby project is an AWS bill nobody noticed growing.

- **AWS Budgets** — a simple rule that says "tell someone the moment real spending this month crosses $42, then $46". It checks the real bill, not a guess.
- **SNS (Simple Notification Service)** — the messenger. When Budgets wants to shout "we crossed a line!", it doesn't know who's listening — it just shouts into a megaphone (a "topic"), and anyone who signed up to that megaphone (an email address, or a small program) hears it.
- **Lambda** — a tiny program that only exists for the seconds it's needed and costs nothing while asleep. Ours wakes up when spending crosses **$48** and does exactly one thing: turns off every Kaval computer it can find — never deletes anything, just switches it off, the way you'd turn off a light rather than smash the switch.
- **CloudWatch Logs** — the diary. Every time that Lambda wakes up, it writes down what it did, so there's a paper trail instead of a mystery.

This whole guard was built and tested — including actually pressing the alarm by hand to watch it fire — *before* a single other AWS resource existed. The guard was hired before there was anything to steal.

## The land and the street

Every computer AWS gives you has to live somewhere. That "somewhere" is a private, walled-off section of AWS that only we can see into.

- **VPC (Virtual Private Cloud)** — our own fenced plot of land inside AWS, invisible to every other AWS customer. Nothing here is shared with a stranger by accident.
- **Subnets** — streets inside that plot. We have three, one in each of three physically separate buildings AWS calls "availability zones" — so if one building has a problem, our stuff can still run from another.
- **Internet Gateway** — the one gate in the fence that lets traffic in and out to the wider internet (so our computers can download updates and talk to Slack).
- **Route table** — the signpost at every street corner telling traffic which way leads out through the gate.

One thing we deliberately did *not* rent: a **NAT Gateway**. It's AWS's "private phone line out" for computers that aren't supposed to be reachable from outside — useful, but it costs about **$32 a month** by itself, more than our entire budget for everything else combined. Instead, our computers sit on the public street but have no doors anyone outside can knock on (see "locks on the doors" below) — cheaper, and just as safe for what we need.

## The computers themselves

We rent three separate computers, each doing one job, never mixed together:

- **The app node** — a small ARM computer (`t4g.medium`) that runs the actual Kaval program: the detective, the scout, the hands, and the receptionist (see [Inside the cluster](#pods-postgres)). It was rented as **"spot"** — a much cheaper rate AWS offers for computers it's allowed to take back with two minutes' warning — until AWS twice had none to give, so it is now rented at the normal ("on-demand") rate; the rebuild-itself design below is still what makes replacement painless. We accept that risk because we built something that notices and rebuilds itself within minutes (an Auto Scaling Group, below) — turning an outage into a short blip instead of a 2am phone call.
- **The database server** — its own separate computer (`t4g.small`) whose only job is running the filing cabinet (Postgres, see [Inside the cluster](#pods-postgres)). It's rented normally, not spot, because a filing cabinet that might vanish with two minutes' notice is not a filing cabinet you can trust.
- **The dev server** — a third computer, used only while actually building and testing, that puts itself to sleep automatically after an hour of nobody using it, so it's nearly free most of the time.

**Auto Scaling Group** — the app node isn't just "a computer", it's a standing instruction to AWS: "always have exactly one of these running; if it disappears, build another one immediately, from the same recipe." That recipe includes a small startup script that reinstalls everything the computer needs (Kubernetes, our deployment tool) the moment it boots, so a replacement computer rebuilds the whole running system on its own, with nobody watching.

## The locks on the doors

**Security groups** are AWS's firewalls — a list of exactly which doors are allowed to be knocked on, and by whom. Every computer we rent has one, and the list is short on purpose:

- The app node: no doors open to the outside world at all. Nothing can knock on it from the internet.
- The database server: exactly one door, and it only opens for the app node specifically — nothing else, not even us directly, is allowed to knock on it over the network.

Neither computer has an "SSH door" (the traditional way people log into a remote computer) open to anyone. See the next section for how we get in instead.

## The remote control, not a spare key under the mat

**SSM Session Manager** is how we actually get a terminal on these computers without ever opening a door for it. Instead of AWS listening for someone to knock, our computer calls *out* to AWS on its own, AWS checks our identity, and only then connects us through — like a computer that only lets you remote-control it after it has dialled AWS itself and vouched for you, rather than leaving its own front door unlocked waiting for anyone with the right key.

**SSM Parameter Store** is the locked little safe sitting next to it — where every database password lives, generated randomly by Terraform and never typed by a person. Reading one requires both permission *and* a decryption key (KMS), and nothing is ever written into this repository's code.

## The photo album for finished programs

**ECR (Elastic Container Registry)** is a locked photo album, one page per service (gateway, agent, executor, collector). When CI finishes building a program, it drops one finished, labelled photo in — and once a label (a tag) is used, it can never be swapped out from under us for a different photo. That's what lets us promise "the exact program tested in staging is the exact program running in prod", because the photo physically cannot change after it's filed.

## ID badges, not master keys

**IAM (Identity and Access Management)** is how every computer and every tiny automatic program proves who it is and gets exactly the badge it needs — never a master key to everything. The app node's badge lets it manage itself over SSM and fetch photos from the album above; nothing more. The money-guard Lambda's badge lets it switch computers off and nothing else — it cannot start one, delete one, or touch anything unrelated to the project.

## Hard drives — and the automatic camera over them

- **EBS (Elastic Block Store)** — the actual hard drives attached to our computers. The database server has two: a small one for the operating system, and a separate, bigger one (20 GB) that holds nothing but the real data. They're kept separate on purpose — rebuilding or replacing the computer never touches the data drive.
- **DLM (Data Lifecycle Manager)** — an automatic camera that photographs (snapshots) that data drive once a day and keeps the last 7 photos, deleting older ones on its own. If the drive is ever damaged, we restore from the most recent photo instead of losing everything.

## The far-away filing cabinet, for a second kind of backup

**S3 (Simple Storage Service)** — a second, completely separate backup target: a bucket (think "a folder that can hold unlimited files") that will hold nightly exports of the database's actual contents, not just a photo of the drive. A drive photo (DLM, above) protects against "the drive broke"; an S3 export protects against "someone's program corrupted the data but the drive is fine" — two different disasters, two different cures. The bucket is locked to the public entirely and encrypts everything inside it automatically.

Getting data into that bucket doesn't even leave AWS's own network — a free **VPC endpoint** gives our computers a direct private road straight to S3, so there's no per-gigabyte internet charge and no trip out through the public internet at all.

## The whole list, at a glance

| Service | In one sentence | What it costs us |
|---|---|---|
| **AWS Budgets + SNS + Lambda** | Watches spending and turns everything off at $48 | Free — a Lambda that almost never runs costs pennies |
| **VPC, subnets, internet gateway** | Our own private, fenced-off street inside AWS | Free |
| **App node (EC2, Auto Scaling Group)** | Runs the actual Kaval program | About $16 a month if left on around the clock; On-Demand since 2026-10-08 because the cheaper spot rate kept having no capacity |
| **Database server (EC2)** | Its own computer, just for Postgres | ~$14.40/mo all-in (ADR-0008) |
| **Security groups** | Firewalls — who's allowed to knock | Free |
| **SSM Session Manager + Parameter Store** | Remote control with no open doors, plus a password safe | Free |
| **ECR** | Locked photo album of finished programs | Pennies for storage |
| **IAM** | ID badges, never master keys | Free |
| **EBS volumes + DLM snapshots** | Hard drives, plus a daily automatic photo of the data one | A couple of dollars a month |
| **S3 + VPC endpoint** | A second, offsite backup of the data itself | Pennies, until it's full of backups |

Nothing on this page is a diagram or a plan — every one of these is real, switched on, and currently part of the bill. The [System topology](#system) card on the board shows the same facts again, drawn as boxes and arrows with the exact credentials each one holds, for when a denser view is more useful than a plain-language one.
