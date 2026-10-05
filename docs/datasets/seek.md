# Pushing to FAIRDOM-SEEK

A dataset whose profile describes an ISA structure can be pushed to a FAIRDOM-SEEK instance. The hub creates the profile's Sample Types and controlled vocabularies on the instance, then creates the dataset's records in one SEEK project.

## The SEEK page

**SEEK** in the header opens the page that holds everything described here, in the order it has to happen:

1. **Connection** shows whether your SEEK account is connected, and holds the form to connect it.
2. **Project** shows the SEEK project your pushes go to, and lets you change it.
3. **Datasets** lists every dataset you can open whose specification can be pushed, each with **ISA templates**, **Check SEEK** and **Push to SEEK**. The result of a check or a push appears in the dataset's row.

A step that is not done yet says what is missing. Until a connection is stored and working, the dataset actions that need it are disabled and say so; **ISA templates** needs no connection and is always available.

Below the list, the page states how many of your datasets cannot be pushed because their specification has no Investigation entity.

The same three actions are in the sidebar of each dataset that can be pushed, together with a **SEEK** link to this page.

## Which datasets can be pushed

The dataset sidebar shows the SEEK controls when the dataset's specification has an entity with the SEEK role `Investigation`. SEEK attaches every record to an Investigation, so a specification without that role has no structure to map onto; ENA, PRIDE and MIAPPE datasets therefore show no SEEK controls.

The hub reads the roles from the specification the dataset is bound to, wherever it is stored: a built-in profile, one of your drafts, or a published specification. A dataset created from a draft uses the draft as it is now.

## Connecting your SEEK account

The connection is set in the **Connection** step of the SEEK page. Your profile page shows its standing and links there.

1. Enter the base URL of the instance and an API key. A key is created in SEEK under your profile, **API tokens**.
2. Click **Save and check**. The hub asks the instance for your projects and records the result.
3. Choose the **Project** that pushes go to.

The instance must be reachable from the hub server. The key is stored encrypted and is never shown again; leaving the key field blank keeps the stored one. Records are created as the person the key belongs to, which is why each user has their own connection.

## Installing the ISA Templates

SEEK's ISA-JSON export reads an ISA Template for each Sample Type. Templates cannot be installed over the API: a SEEK administrator installs them once per specification and version.

1. Click **ISA templates** for the dataset, on the SEEK page or in the dataset sidebar. The hub builds the templates from the dataset's specification and downloads them as one JSON file.
2. A SEEK administrator uploads the file under **Templates**, **Populate Templates**. The upload runs as a background job, and uploading again keeps the templates that exist.

The **Populate Templates** page exists only when *Compliance with ISA-JSON schemas* is enabled on the instance, which requires *Single page*, *ISA* and *Samples*.

The download is refused for a specification that cannot be pushed, and for one with no entities that describe samples, since there is no material chain to build templates from.

## Checking the instance

**Check SEEK** reports which of the specification's three templates (study source, study sample, assay) are installed, and names the missing ones. A connection that does not answer is reported as such, with the cause.

## Pushing

**Push to SEEK** provisions the specification on the instance and creates the dataset's records in the chosen project. The panel reports what was created, or why the push failed.

Tick **downloadable in SEEK** to give the records SEEK's *download* sharing level, which the ISA-JSON export requires. Unticked, the records stay private to your SEEK account.

The hub does not import from SEEK.
