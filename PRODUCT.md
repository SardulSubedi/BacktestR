# BacktestR

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

People who describe stock trading ideas in everyday English, and technical users who supply complete rules. They need to inspect the interpretation and test it before exporting execution code.

## Product Purpose

Turn a description into a reviewable portfolio strategy, clarify missing decisions, disclose assumptions, test the strategy over varied historical periods, and export C++ execution code.

## Operating Context

The user confirmed a local browser application for daily US-stock strategies, CSV import, downloadable C++ code, and Alpaca as the initial data and broker integration.

## Capabilities and Constraints

The first implementation uses an explicit, validated rule representation. Unknown rules must be clarified or rejected. Real AI interpretation needs a provider credential. Demonstration prices are synthetic and cannot establish performance. Historical tests must account for signal timing and costs. C++ export must describe its actual broker and execution support accurately.

## Stack

Delegated: the user accepted the recommended Python standard-library local API, HTML/CSS/JavaScript browser interface, and C++20 export. This keeps the core install independent of package downloads. The user also accepted a clear research-notebook interface.

## Evidence on Hand

The supplied GitHub repository has no advertised refs. The local folder has no application files. There are no historical price files, credentials, brand assets, or measured performance claims.

## Product Principles

- Make each strategy decision inspectable.
- Ask about material gaps and disclose every default.
- Use identical rules in simulation and exported code.
- Distinguish demonstration data from historical evidence.
- Report uncertainty and failures as clearly as returns.
