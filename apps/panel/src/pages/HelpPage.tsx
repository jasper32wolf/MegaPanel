import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { glossary, helpCategories, helpTopics, type HelpTopic } from "../content/help";
import { EmptyState, PageHeader, Surface } from "../components/ui";

function topicMatches(topic: HelpTopic, query: string) {
  const normalized = query.trim().toLocaleLowerCase("ru");
  if (!normalized) return true;
  return [topic.title, topic.summary, topic.category, ...topic.aliases, ...topic.terms]
    .join(" ")
    .toLocaleLowerCase("ru")
    .includes(normalized);
}

export function HelpPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const activeTopicId = searchParams.get("topic");
  const activeTopic = helpTopics.find((topic) => topic.id === activeTopicId) || null;
  const projectId = searchParams.get("project");
  const validProjectId = projectId && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(projectId) ? projectId : null;
  const visibleTopics = useMemo(() => helpTopics.filter((topic) => topicMatches(topic, query)), [query]);

  function topicLink(topic: HelpTopic) {
    if (!validProjectId) return topic.link;
    const routes: Record<string, string> = {
      "project-workflow": `/projects/${validProjectId}`,
      "business-facts": `/projects/${validProjectId}/facts`,
      research: `/projects/${validProjectId}#research`,
      "pages-and-qa": `/projects/${validProjectId}#plans`,
      "ai-preparation": `/projects/${validProjectId}#ai`,
      "candidate-and-publish": `/projects/${validProjectId}/releases`,
    };
    return routes[topic.id] || topic.link;
  }

  function openTopic(topic: HelpTopic) {
    setSearchParams((current) => {
      current.set("topic", topic.id);
      return current;
    });
  }

  function closeTopic() {
    setSearchParams((current) => {
      current.delete("topic");
      return current;
    });
  }

  return (
    <div>
      <PageHeader title="Справка оператора" description="Понятные инструкции по работе в панели. Справка объясняет действия и их границы, но не заменяет инфраструктурные runbooks для VPS, DNS, сертификатов и восстановления." />
      <Surface title="Найдите нужную задачу">
        <label className="field">Поиск по справке и терминам<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Например: черновик, заявка, публикация, AI, очередь" /></label>
        <p className="muted">Перед публикацией всегда проверяйте данные, качество, закрытый предпросмотр и юридическое решение. Панель не публикует сайт сама.</p>
      </Surface>
      {activeTopic ? <Surface title={activeTopic.title}>
        <p><strong>{activeTopic.summary}</strong></p>
        <h3>Что это</h3><p>{activeTopic.whatItDoes}</p>
        <h3>Что не происходит автоматически</h3><p>{activeTopic.notAutomatic}</p>
        <h3>До начала</h3><ul>{activeTopic.prerequisites.map((item) => <li key={item}>{item}</li>)}</ul>
        <h3>Порядок действий</h3><ol>{activeTopic.steps.map((item) => <li key={item}>{item}</li>)}</ol>
        <h3>Если что-то пошло не так</h3>{activeTopic.troubleshooting.map((item) => <div key={item.symptom}><strong>{item.symptom}</strong><p>{item.action}</p></div>)}
        <h3>Связанные понятия</h3><ul>{activeTopic.terms.map((term) => <li key={term}><strong>{term}</strong> — {glossary[term] || "Описание появится в словаре."}</li>)}</ul>
        <div className="row"><Link className="btn" to={topicLink(activeTopic)}>{activeTopic.linkLabel}</Link><button className="btn btn-ghost" type="button" onClick={closeTopic}>Вернуться ко всем разделам</button></div>
      </Surface> : <>
        {helpCategories.map((category) => {
          const topics = visibleTopics.filter((topic) => topic.category === category);
          if (!topics.length) return null;
          return <Surface key={category} title={category}>
            <div className="help-grid">{topics.map((topic) => <article className="help-card" key={topic.id}><h3>{topic.title}</h3><p>{topic.summary}</p><button className="btn btn-ghost" type="button" onClick={() => openTopic(topic)}>Открыть инструкцию</button></article>)}</div>
          </Surface>;
        })}
        {!visibleTopics.length && <EmptyState title="Ничего не найдено" hint="Попробуйте более короткое слово или термин из рабочего процесса." />}
        <Surface title="Словарь терминов">
          <p className="muted">Технические названия оставлены только там, где они помогают найти сохранённый артефакт или статус. Здесь они объяснены простым языком.</p>
          <dl className="help-glossary">{Object.entries(glossary).map(([term, definition]) => <div key={term}><dt>{term}</dt><dd>{definition}</dd></div>)}</dl>
        </Surface>
      </>}
    </div>
  );
}
