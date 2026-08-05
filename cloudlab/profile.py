"""CloudLab profile for the MANTA NSDI'27 artifact."""

import geni.portal as portal
import geni.rspec.pg as rspec


pc = portal.Context()
pc.defineParameter(
    "nodes",
    "Number of physical nodes",
    portal.ParameterType.INTEGER,
    10,
    longDescription="Number of CloudLab raw PCs to reserve.",
)
pc.defineParameter(
    "repo_url",
    "Artifact repository URL",
    portal.ParameterType.STRING,
    "https://github.com/michaelwcl323/manta_nsdi27",
)
pc.defineParameter(
    "ref",
    "Git ref to checkout",
    portal.ParameterType.STRING,
    "artifact-evaluation",
)
pc.defineParameter(
    "node_type",
    "CloudLab hardware type",
    portal.ParameterType.STRING,
    "r320",
    longDescription="Optional CloudLab hardware type. Leave empty to let CloudLab choose any available raw PC.",
)

params = pc.bindParameters()
request = pc.makeRequestRSpec()

bootstrap = (
    "sudo bash -lc "
    "'set -euxo pipefail; "
    "rm -rf /local/repository; "
    "git clone {repo_url} /local/repository; "
    "cd /local/repository; "
    "git fetch --tags origin; "
    "git checkout {ref}; "
    "./scripts/environment_setup.sh; "
    "touch /local/bootstrap.done' "
    "> /local/bootstrap.log 2>&1 || "
    "(touch /local/bootstrap.failed; exit 1)"
)

for index in range(params.nodes):
    node = request.RawPC("node-%d" % index)
    node.disk_image = "urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU22-64-STD"
    if params.node_type:
        node.hardware_type = params.node_type
    node.addService(
        rspec.Execute(
            shell="bash",
            command=bootstrap.format(repo_url=params.repo_url, ref=params.ref),
        )
    )

pc.printRequestRSpec(request)
