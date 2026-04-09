# Roadmap To Licensing

## Goal

This roadmap is focused on one concrete objective:

- make the software installable and licensable for a specific client site within 4 months

This is not a roadmap for turning the software into a general-market commercial product. It is a roadmap for delivering a professional client-specific installation with the minimum required legal, technical, operational, and documentation pieces.

The target commercial model is:

- institutional single-site license
- time-limited license
- limited number of seats
- software delivered together with training, calibration logic, workflow implementation, and support

## Scope Decision

To make the 4-month deadline realistic, the project should optimize for:

- one client
- one laboratory site
- one controlled installation target
- one supported operating system profile
- one primary deployment pattern

Recommended deployment pattern for v1:

- install the backend and frontend on one designated Windows machine at the client site
- access through the local browser on that machine, or optionally from the same local network later
- use a signed local license file

Recommended licensing model for v1:

- site-bound license file
- expiry date enforced
- seat count enforced as max named local users in the system

This is simpler and safer than implementing floating concurrent-seat licensing in the first release.

## Definition Of Done

At the end of the 4 months, the software should be deliverable with the following:

- installable on the client machine using a documented and repeatable setup procedure
- license file issued for the client site
- expiry enforcement working
- seat limit working
- admin-visible license status page
- stable scientific workflow for import, diagnostics, calibration, processing, and export
- user manual in PDF
- installation guide
- support contact details
- release package archived internally with version, client, expiry, and seat count

## Current State Summary

The repo is already strong on scientific workflow and internal architecture:

- FastAPI backend exists
- Next.js frontend exists
- session persistence and export workflows exist
- standards database support exists
- refactor docs and acceptance criteria already exist

The main missing pieces for client delivery are:

- licensing system
- install packaging
- reproducible environment setup
- working CI and release validation
- user and seat model
- client handover documentation
- final closure of refactor/parity gaps

## What Is Not Required For This 4-Month Goal

To stay on schedule, the following should not be treated as required for v1:

- public SaaS deployment
- online billing
- self-service customer portal
- marketplace-grade installer for arbitrary machines
- cloud multi-tenancy
- advanced telemetry
- sophisticated floating license server
- enterprise SSO
- support for many operating systems

## Key Product Decisions To Freeze In Week 1

These decisions should be made immediately and not revisited casually:

1. Deployment target
   - Windows only for v1
   - one designated client machine or one designated local server

2. Seat definition
   - recommended: seat = named local user account in the application

3. Site definition
   - one laboratory at one legal/institutional site

4. License enforcement
   - signed offline license file
   - expiry date
   - site binding
   - max named users

5. Upgrade model
   - manual upgrade by you during support period

6. Support model
   - email plus direct contact during the agreed support period

If these stay stable, the 4-month plan is doable.

## Recommended Technical Model

### Installation Model

For v1, the simplest robust model is:

- Python backend installed in a controlled application folder
- built frontend served locally
- app data stored outside the repo in a dedicated application data folder
- license file stored in a protected config location
- startup through a Windows service or a supervised local process

### License Model

The license file should contain:

- license ID
- client institution name
- site name
- site identifier or site address
- issue date
- expiry date
- support-until date
- max seats
- allowed software version or version range
- machine or host binding identifier
- digital signature

### User And Seat Model

Recommended v1 seat model:

- local admin account
- local operator accounts
- total enabled users cannot exceed licensed seat count

Optional stretch goal:

- concurrent seat enforcement

Not recommended for v1 unless time remains.

## 4-Month Roadmap

## Month 1: Stabilize The Product Base

### Objectives

- freeze scope
- make the project reproducible from a clean machine
- identify and close the highest-risk scientific gaps
- make release validation trustworthy

### Deliverables

- release branch created
- dependency installation works from a clean machine
- broken CI replaced with a working pipeline
- backend tests runnable
- frontend production build validated
- short list of parity-critical bugs fixed
- agreed licensing design written down

### Tasks

1. Environment and packaging basics
   - remove reliance on the checked-in virtual environment
   - confirm Python 3.11 installation path assumptions
   - make setup work on a clean Windows machine
   - pin and verify dependencies

2. CI and release validation
   - fix `.github/workflows/ci.yml`
   - add backend test job
   - add frontend build job
   - add one smoke-check script for import to export flow

3. Product gap review
   - resolve open issues in `TODO.md`
   - review remaining Streamlit dependency points
   - classify issues into must-fix and defer

4. Licensing architecture doc
   - define license file schema
   - define seat semantics
   - define site-binding strategy
   - define expiry behavior

### Exit Criteria

- clean install works
- CI works
- release validation can be repeated
- licensing design is frozen

## Month 2: Build Licensing And Local Administration

### Objectives

- implement the minimum viable licensing layer
- add local accounts and seat enforcement
- expose license visibility in the UI

### Deliverables

- signed license file format
- backend license validation on startup
- admin license status endpoint
- local user model
- seat count enforcement
- audit logging for license-relevant actions

### Tasks

1. License engine
   - create signed license file format
   - validate signature at startup
   - validate expiry date
   - validate host binding
   - validate allowed version
   - define failure behavior and warning states

2. User management
   - add admin user
   - add operator users
   - add password storage with proper hashing
   - add login/logout
   - add session management

3. Seat enforcement
   - prevent creation or enabling of users above licensed seat count
   - show remaining seats in admin UI

4. Audit logging
   - record logins
   - record license load or change
   - record user creation and disablement
   - record exports and major processing actions if practical

### Exit Criteria

- app refuses invalid or expired licenses
- app enforces seat count
- admin can inspect license status
- operator access is not anonymous anymore

## Month 3: Installability And Client Delivery Packaging

### Objectives

- turn the software from a dev repo into a client-installable package
- separate app code from client data
- formalize backup and recovery

### Deliverables

- installation layout defined
- installer or scripted install process
- application config file template
- data directory strategy
- backup and restore procedure
- upgrade procedure

### Tasks

1. Installation layout
   - define install directory
   - define data directory
   - define logs directory
   - define config directory
   - define license file location

2. Installer or scripted setup
   - create an installation script or installer
   - install backend dependencies
   - place frontend build
   - write config
   - register startup service
   - load initial admin account
   - install the license file

3. Data handling
   - ensure autosave and session files live in application data, not the repo
   - ensure raw client workbooks are stored predictably
   - confirm backup strategy

4. Upgrade handling
   - define versioned upgrade path
   - preserve data and config across upgrades
   - define rollback procedure

### Exit Criteria

- install can be repeated from scratch
- uninstall and reinstall do not corrupt data unexpectedly
- backup and restore are documented and tested

## Month 4: Documentation, Validation, And Handover Readiness

### Objectives

- lock the release candidate
- validate on a clean target machine
- produce the handover package

### Deliverables

- final release candidate
- user manual PDF
- installation guide
- support contact sheet
- internal issuance checklist
- client-specific license package
- acceptance checklist

### Tasks

1. Validation
   - perform install on a clean machine
   - test license activation
   - test expiry warning behavior
   - test seat enforcement
   - test import, calibration, processing, and export
   - test backup and restore

2. Documentation
   - create user manual PDF
   - create installation guide
   - create administrator guide if needed
   - create release notes

3. Handover package
   - package release build
   - generate client-specific license file
   - record version, expiry, and seat count
   - prepare support contact details
   - prepare formal handover checklist

4. Internal controls
   - create issuance record template
   - create support ticket log template
   - create reissue or replacement procedure for a failed machine

### Exit Criteria

- software installs on a clean client-like machine
- license works
- documentation exists
- release package is ready to hand over

## Priority Breakdown

### Must Have

- reproducible install
- valid backend and frontend build pipeline
- stable scientific workflow
- signed local license file
- expiry enforcement
- seat enforcement
- local user accounts
- installation guide
- user manual
- support contact details

### Strongly Recommended

- audit logging
- admin license page
- backup and restore script
- machine replacement procedure

### Can Be Deferred

- floating concurrent seats
- remote license revocation
- cloud sync
- self-service admin portal
- multi-client deployment tooling

## Major Risks

### Risk 1: Scope Creep

The biggest risk is treating this as a general commercial platform instead of a single-client deliverable.

Mitigation:

- freeze scope in week 1
- defer marketplace features
- defer cloud features

### Risk 2: Refactor Instability

The project is still carrying parallel app surfaces and some Streamlit compatibility assumptions.

Mitigation:

- prioritize parity-critical fixes in month 1
- release from one chosen surface only
- treat Streamlit as fallback, not as the release face

### Risk 3: Environment Reproducibility

The current local Python environment is not yet reliable enough for client installation.

Mitigation:

- standardize Python version
- test on a clean machine early
- stop relying on local development state

### Risk 4: Over-Engineering Licensing

Trying to build a sophisticated license server could consume the entire schedule.

Mitigation:

- use an offline signed license file
- enforce named seats, not floating seats

### Risk 5: Documentation Left To The End

If manuals and install steps are postponed too long, the handover will feel unfinished.

Mitigation:

- draft documents in month 3
- refine them in month 4

## Feasibility Assessment

## Is 4 Months Doable?

Yes, 4 months is doable if the scope stays disciplined.

It is realistic if the project is treated as:

- a specific client installation
- one operating environment
- one licensing model
- one controlled release process

It is not realistic in 4 months if the target expands into:

- a polished general-market desktop product
- cross-platform packaging
- enterprise-grade license server
- cloud billing and self-service customer management

## Conditions For Success

This 4-month plan is realistic under these assumptions:

1. The scientific workflow is already mostly correct and only needs focused stabilization.
2. The first release supports one controlled installation pattern.
3. Seat enforcement is implemented as max named users, not floating concurrent users.
4. You are willing to defer non-essential commercial polish.
5. At least the final month includes access to a clean test machine resembling the client environment.

## Schedule Confidence

- high confidence: installable client-specific licensed release in 4 months
- medium confidence: same plus strong admin UX and polished documentation
- low confidence: same plus sophisticated floating licensing, remote update system, and general-market readiness

## Recommended Immediate Next Steps

1. Approve the v1 scope:
   - Windows only
   - single-site
   - named-seat licensing
   - offline signed license file

2. Create implementation epics:
   - environment and CI
   - product stabilization
   - licensing
   - users and seats
   - packaging
   - documentation and handover

3. Start month 1 with these concrete outputs:
   - fixed CI
   - clean install on a fresh machine
   - written license schema
   - written seat definition
   - must-fix bug shortlist

## Final Recommendation

Proceed with the 4-month target.

The goal should be:

- a client-ready institutional installation
- not a general-market software product

If scope stays tight, this is achievable.

If scope expands into broader commercial infrastructure, the timeline will become risky very quickly.
