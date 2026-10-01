"""The recommender's training code: data, association rules, offline evaluation, artefacts.

Self-contained on purpose: it runs on an Azure ML curated environment (pandas, MLflow) without
the API's dependencies, so every import inside the package is relative. Locally it is imported
as `src.ml.reco`; on Azure ML the code root is `src/ml`, so it is imported as `reco`.
"""
