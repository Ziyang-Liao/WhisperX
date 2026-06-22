# Diagrams

Rendered diagrams used in the docs. Sources are committed so they're reproducible.

| File | What | Source |
| --- | --- | --- |
| `architecture_aws.png` | AWS architecture (real service icons) | `architecture_aws.py` → `main()` |
| `deployment_aws.png` | Deployment topology / locked-down origin | `architecture_aws.py` → `deployment()` |
| `sequence_end_to_end.png` | UML sequence (upload → transcribe → translate → download) | Mermaid block in [`../architecture.md`](../architecture.md) §2 |

## Regenerate

AWS-icon diagrams (need Graphviz `dot` on PATH):

```bash
pip install diagrams
python docs/diagrams/architecture_aws.py
```

Sequence diagram (from the Mermaid source in `architecture.md`):

```bash
npx @mermaid-js/mermaid-cli -i seq.mmd -o sequence_end_to_end.png -b white -s 2
```
