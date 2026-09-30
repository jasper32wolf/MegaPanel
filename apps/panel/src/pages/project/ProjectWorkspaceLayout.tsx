import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";

const sections = [
  ["overview", "Overview"],
  ["facts", "Facts"],
  ["pages", "Pages"],
  ["releases", "Releases"],
  ["routing", "Routing"],
  ["activity", "Activity"],
] as const;

export function ProjectWorkspaceLayout({ projectId, children }: { projectId: string; children: ReactNode }) {
  return (
    <div className="project-workspace">
      <nav className="row" aria-label="Project workspace sections">
        {sections.map(([slug, label]) => (
          <NavLink
            key={slug}
            className={({ isActive }) => `btn btn-ghost${isActive ? " is-active" : ""}`}
            to={`/projects/${projectId}/${slug}`}
          >
            {label}
          </NavLink>
        ))}
        <Link className="btn btn-ghost" to="/projects">К проектам</Link>
      </nav>
      {children}
    </div>
  );
}
