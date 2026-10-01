"""Domain packages: domain-specific contracts layered onto the domain-neutral core.

The architecture is domain-neutral on purpose (`docs/domain-neutral-system-design.md`). A domain
package contributes **vocabulary and types**, never a second dispatch path, a parallel authorisation
seam or a private route to memory. Anything that needs to execute belongs in the generic
`debugagent.agents` roster instead, and only if the roadmap's worker-justification test is satisfied.

Each domain package states its own trust boundary in its module docstring, and the test suite asserts
those boundaries by inspecting imports rather than by trusting the prose.
"""