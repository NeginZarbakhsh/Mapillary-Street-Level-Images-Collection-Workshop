# PDF → text → Azure Blob Storage

Two scripts. Step 1 runs entirely on your machine and costs nothing. Step 2
uses the cheapest Azure service that exists — not Azure AI Search, not
Azure OpenAI — because those are the ones that cost real money (see
`governance-document-analysis/RESEARCH.md` for exactly why). Blob Storage is
plain file storage, priced at **$0.018 per GB per month** — for a few hundred
PDFs, that's a few cents a month, not dollars.

## ⚠️ Read this before you upload anything real

You said your Azure access is a **free trial / training sandbox** — the kind
that expires in a few hours and resets. If that's true of the one you have:

- **Everything you upload will likely be deleted when it expires or resets.**
- Treat it as a place to *practice the steps*, not a place to *keep your documents*.
- Your original PDFs are always safe on your own computer regardless — this
  only affects the *copy* you upload to Azure.

If you need somewhere that actually keeps your files long-term and you don't
have a persistent Azure subscription, the honest answer is: **your own
computer, in a folder** is the $0, zero-expiry option. Use Azure for this only
once you know your storage account will still be there next week.

---

## Step 1: PDF → text (free, local, no account needed)

```bash
pip install -r requirements.txt
```

Put your PDFs in the `pdfs/` folder, then:

```bash
python3 pdf_to_text.py
```

Every PDF in `pdfs/` becomes a `.txt` file in `text_output/`, with page
markers kept in (`[page 8]`) so you don't lose that information. Already
tested against your sample document — see the file in `text_output/` right
now if you want to check the output before running it on the rest.

Scanned (image-only) PDFs get skipped with a message telling you why — this
script can't OCR them.

---

## Step 2: Get an Azure Blob Storage account (portal, ~5 minutes)

1. Go to **[portal.azure.com](https://portal.azure.com)** and log in with your
   sandbox credentials.
2. In the top search bar, type **"Storage accounts"** → click it → **+ Create**.
3. Fill in the form:
   - **Resource group** — your sandbox probably already gave you one; pick it
     from the dropdown rather than creating a new one.
   - **Storage account name** — has to be globally unique, lowercase, no
     spaces, e.g. `govdocs2026negin`.
   - **Region** — whichever is closest to you / already selected.
   - **Performance** — Standard.
   - **Redundancy** — **Locally-redundant storage (LRS)**. This is the
     cheapest option; for a pilot with documents you also have local copies
     of, you don't need anything fancier.
4. Click **Review + create**, then **Create**. Wait about a minute.
5. Once it's done, open the storage account. In the left sidebar, click
   **Containers** → **+ Container**.
   - Name it something like `contracts`.
   - **Public access level: Private (no anonymous access)** — these could be
     sensitive documents; don't make them public.
   - Create.
6. Still in the left sidebar, click **Access keys**. Under **key1**, click
   **Show**, then copy the **Connection string**. This is your credential —
   treat it exactly like a password. Don't paste it in chat, don't commit it
   to GitHub.

## Step 3: Give the script that connection string

```bash
export AZURE_STORAGE_CONNECTION_STRING="paste the connection string here"
```

## Step 4: Upload

```bash
python3 azure_blob.py upload text_output --container contracts
```

Uploads every file from `text_output/` into the `contracts` container. It
creates the container automatically if step 2's container name doesn't match
— but the manual portal steps above let you *see* it exists before trusting
the script with it.

Check what's actually there:

```bash
python3 azure_blob.py list --container contracts
```

---

## Step 5: "Once I upload it, how do I get it from a URL?"

Every blob technically has a URL shaped like:

```
https://<your-account-name>.blob.core.windows.net/contracts/mydoc.txt
```

**But since the container is private (which it should be), that URL does
nothing on its own** — pasting it in a browser gives you an error, not your
file. Private is the correct default for real documents; you have two ways to
actually get the content out:

**Option A — a temporary link (use this if you need to share a link, or fetch
the file with something that isn't this script):**

```bash
python3 azure_blob.py url mydoc.txt --container contracts --hours 24
```

This prints a full URL with a signed token stuck on the end (`?sv=...`) —
that token is what makes it work despite the container being private, and it
stops working after the number of hours you gave it. Paste that whole link
(not the plain one from above) into a browser or `curl` and it downloads the
file. Nobody can reuse it after it expires.

**Option B — just download it directly (simplest, if you're already in Python):**

```bash
python3 azure_blob.py download mydoc.txt --container contracts --out mydoc.txt
```

No URL involved at all — this authenticates with your connection string and
pulls the bytes straight down.

---

## What this does and doesn't solve

- **Solves:** getting many PDFs into text, and somewhere off your laptop if
  you need that, for close to $0.
- **Doesn't solve:** actually *analysing* the documents (voting rights,
  control, etc.) — that's `governance-document-analysis/` in this repo, and
  it still needs an Anthropic API key regardless of where the source files
  live.
- **Doesn't solve:** search across many documents at once — Blob Storage
  just stores files, it doesn't index or embed them. If you get to the point
  of needing to search across hundreds of documents, that's when the
  RAG pipeline in `governance-document-analysis/` (or Azure AI Search /
  MongoDB Atlas, per `RESEARCH.md`) becomes relevant — not before.
