# Judges' live link: Cloud Run + a private Cloud Storage bucket

**Live (deployed 2026-10-31):** https://coolorbit-dashboard-762928923551.us-east1.run.app
project `scienceclaw26bos-3013`, region `us-east1`, bucket `gs://coolorbit-dash-scienceclaw26bos-3013` (prefix
`dashboard/`). Deleted with the project Sunday night; GitHub stays the permanent copy.

**What the event's GCP setup allowed (and what we did):** the org blocks service-account key creation
(`constraints/iam.disableServiceAccountKeyCreation`), and the lab account can't grant impersonation on a
service account (`iam.serviceAccounts.setIamPolicy` denied). Bucket-level IAM does work, so the GB10 pushes as
**Madhan's own lab account `devstar3007@gcplab.me`, which has `roles/storage.objectAdmin` on this bucket only** (no
project roles in `scienceclaw26bos-3013`), with `push_exports --via gcloud`. The `dashboard-push` service account
exists, has no keys, and is unused. Cloud Run reads with `dashboard-run` (objectViewer on this bucket only).

## GB10, as actually set up
```bash
gcloud auth login --no-browser          # devstar3007@gcplab.me (bucket-only write access)
python -m dashboard.push_exports --bucket coolorbit-dash-scienceclaw26bos-3013 \
    --board boards/science-1 --board boards/eval/<run> --every 180
```

```
GB10 (every few minutes)                         Cloud Storage (private)              Cloud Run (public URL)
python -m dashboard.push_exports  --(bucket-only key)-->  gs://<bucket>/dashboard/*.json  <--(read-only SA)--  dashboard/server.py
```

- The bucket is **private** (uniform access, public access prevention). Only two service accounts touch it:
  `dashboard-push` (the GB10; `roles/storage.objectAdmin` on **this bucket only**, needed to overwrite objects) and
  `dashboard-run` (Cloud Run; `roles/storage.objectViewer` on this bucket only).
- The GB10's key is for `dashboard-push` only, stored outside the repo (e.g. `~/.config/coolorbit/dashboard-push.json`,
  `chmod 600`), handed over privately (never git, never chat). `.env`, `*.json` keys and `dashboard/data/` stay out of git.
- `push_exports` refuses to publish any SYNTHETIC board, uploads per-board files first and `index.json`/`data.js`
  last, and never deletes objects.
- Team limits: max 2 GCP accounts per team; Cloud Run and GCS are deleted Sunday night. GitHub stays the permanent
  copy (the boards themselves are committed; the export can be regenerated with `python -m dashboard.export`).

## One-time setup (whoever holds the team GCP login)

```bash
PROJECT=<project-id>; REGION=us-east1
BUCKET=coolorbit-dash-$PROJECT                       # globally unique
gcloud config set project $PROJECT
gcloud services enable run.googleapis.com storage.googleapis.com cloudbuild.googleapis.com \
    artifactregistry.googleapis.com iam.googleapis.com

gcloud storage buckets create gs://$BUCKET --location=$REGION \
    --uniform-bucket-level-access --public-access-prevention

gcloud iam service-accounts create dashboard-run  --display-name="Cool Orbit dashboard (reads exports)"
gcloud iam service-accounts create dashboard-push --display-name="Cool Orbit GB10 (pushes exports)"
RUN_SA=dashboard-run@$PROJECT.iam.gserviceaccount.com
PUSH_SA=dashboard-push@$PROJECT.iam.gserviceaccount.com
gcloud storage buckets add-iam-policy-binding gs://$BUCKET --member=serviceAccount:$RUN_SA  --role=roles/storage.objectViewer
gcloud storage buckets add-iam-policy-binding gs://$BUCKET --member=serviceAccount:$PUSH_SA --role=roles/storage.objectAdmin

# Key for the GB10 (bucket-only). If the org blocks key creation, use the fallback below instead.
gcloud iam service-accounts keys create ~/dashboard-push.json --iam-account=$PUSH_SA

gcloud run deploy coolorbit-dashboard --source dashboard --region $REGION \
    --service-account $RUN_SA --allow-unauthenticated \
    --set-env-vars BUCKET=$BUCKET,PREFIX=dashboard \
    --memory 256Mi --max-instances 3
```

## On the GB10

```bash
pip install google-cloud-storage
mkdir -p ~/.config/coolorbit && mv dashboard-push.json ~/.config/coolorbit/ && chmod 600 ~/.config/coolorbit/dashboard-push.json
export GOOGLE_APPLICATION_CREDENTIALS=~/.config/coolorbit/dashboard-push.json
python -m dashboard.push_exports --bucket <bucket> --board boards/science-1 --board boards/eval/<run> --every 180
```

Fallback if key creation is blocked: on the GB10, `gcloud auth application-default login
--impersonate-service-account=$PUSH_SA` with a team account that has `roles/iam.serviceAccountTokenCreator` on
`dashboard-push` (no key file at all).

## Check, then tear down after the event
```bash
curl -s https://<service-url>/health                  # ok (Cloud Run reserves /healthz)
gcloud run services delete coolorbit-dashboard --region $REGION
gcloud storage rm -r gs://$BUCKET
gcloud iam service-accounts keys list --iam-account=$PUSH_SA   # then delete the key, or the account
```
