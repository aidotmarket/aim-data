# AIM Data Install Guide

AIM Data runs on your own hardware. You bring your data, you bring your AWS account, and AIM Data handles the connection to the ai.market marketplace so buyers can find and license what you have.

This guide gets you from zero to a working install in about thirty minutes if you have Docker ready and an AWS account.

## What you need first

A machine with Docker Desktop or docker engine. Around 16GB of RAM is enough for most workloads. Port 8080 should be free.

If you plan to publish data from S3, an AWS account with an S3 bucket and the ability to create IAM roles. The setup walkthrough covers this.

A serial and a bootstrap token from me. I send these per customer when you activate.

## Get the install file

Grab the docker compose file from GitHub. That is the only file you need from the repo.

```
curl -O https://raw.githubusercontent.com/aidotmarket/aim-data/main/docker-compose.aim-data.yml
```

## Set up your .env

Create a file called `.env` next to the compose file.

Some values you generate yourself. These are random strings and the values are your own. The Postgres password is your choice and you do not need to share it with anyone.

```
POSTGRES_PASSWORD=pick-a-strong-password
VECTORAIZ_SECRET_KEY=generate-a-random-32-byte-string
AIM_DATA_APIKEY_HMAC_SECRET=generate-a-random-32-byte-string
AIM_DATA_KEYSTORE_PASSPHRASE=generate-a-random-32-byte-string
AIM_DATA_INTERNAL_API_KEY=generate-a-random-string
```

For the random strings, this works on any unix shell:

```
openssl rand -hex 32
```

Values I send you per activation. You will get these from me when you sign up.

```
AIM_DATA_SERIAL=...
AIM_DATA_BOOTSTRAP_TOKEN=...
```

Activation is automatic at first boot. The container reads both values, calls home once to register with ai.market, and clears the bootstrap token from memory after a successful activation. After that point you only need your serial.

AIM Data routes the embedded assistant through ai.market, so you do not need your own Anthropic API key. If you have an `ANTHROPIC_API_KEY` line in an older `.env`, it is safe to remove.

And the connection setting that points the install at the marketplace.

```
AIM_DATA_AI_MARKET_URL=https://api.ai.market
```

## Bring it up

```
docker compose -f docker-compose.aim-data.yml up -d
```

Wait about a minute for Postgres and Qdrant to settle. Then check the health endpoint.

```
curl http://localhost:8080/api/health
```

If you see `status: ok` in the response, you are running.

## First admin login

Open `http://localhost:8080` in your browser. You will see a screen to create your admin account. That admin is you, with full access. Save the password somewhere safe because there is no reset path on the install side.

## Connect to ai.market

Inside the app, open Settings then Marketplace. The install needs to register itself as a seller on ai.market.

The registration flow asks for your name, your billing email, and any business details ai.market needs about you as a counterparty. Once you submit, ai.market sends a confirmation email. Click the link and your install shows up as a seller.

You can publish datasets after this point.

## Set up the S3 connector

If your data lives in S3, AIM Data does not move or copy it. The install assumes an IAM role in your AWS account and reads files in place. You keep your AWS credentials, and you can revoke access at any time.

Inside the app, open Data Sources then Add S3 Source. The wizard shows you a trust policy in JSON with the ai.market AWS account as the trusted principal. Copy that JSON, open the IAM console in your own AWS account, and create a new role with S3 read access to your data bucket. Paste the trust policy in as the role's trust relationship. Then copy the role ARN back into AIM Data and click Verify.

Your long lived AWS credentials stay in your account. AIM Data only holds a short lived assumed-role session when it reads.

Once green, you can point AIM Data at any bucket and prefix you have read access to. Files appear in the catalog and you can list them on the marketplace.

## Updating

When I release a new version, you pull and recreate.

```
docker compose -f docker-compose.aim-data.yml pull
docker compose -f docker-compose.aim-data.yml up -d
```

Your data, your settings, and your registration carry across versions.

## Things to know

The keystore passphrase signs your marketplace requests. If you ever lose it, you regenerate one. Your seller identity on ai.market gets a fresh wallet, which means you lose attribution on existing listings. Keep a copy of the passphrase somewhere safe.

I am running this for a small number of customers right now. If anything breaks, email me and I will fix it the same day.
