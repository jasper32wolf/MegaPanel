import { Link, useParams } from "react-router-dom";
import { PageHeader, Surface } from "../../components/ui";
import { ProjectWorkspaceLayout } from "./ProjectWorkspaceLayout";

const copy = {
  facts: {
    title: "Project facts",
    description: "Business facts and private-data boundaries remain in the compatibility workflow.",
    hint: "Open the legacy workspace to edit or confirm facts. This view intentionally does not issue a second project-data request.",
  },
  pages: {
    title: "Project pages",
    description: "Page plans, drafts, AI proposals and QA remain candidate-first.",
    hint: "Open the legacy workspace to manage plans and drafts. No mutation is duplicated in this deep link.",
  },
  releases: {
    title: "Project releases",
    description: "Candidate builds, private preview, legal review and publication are separate actions.",
    hint: "Open the legacy workspace to inspect or operate releases. Publication still requires the existing confirmations.",
  },
  routing: {
    title: "Lead routing",
    description: "Encrypted recipients are kept behind the existing private-data boundary.",
    hint: "Open the legacy workspace to create, review or activate a routing policy.",
  },
} as const;

type Section = keyof typeof copy;

export function ProjectSectionPage({ section }: { section: Section }) {
  const { projectId = "" } = useParams();
  const content = copy[section];
  return (
    <ProjectWorkspaceLayout projectId={projectId}>
      <PageHeader title={content.title} description={content.description} />
      <Surface title="Compatibility workflow">
        <p className="muted">{content.hint}</p>
        <Link className="btn" to={`/projects/${projectId}`}>Открыть workspace</Link>
      </Surface>
    </ProjectWorkspaceLayout>
  );
}
