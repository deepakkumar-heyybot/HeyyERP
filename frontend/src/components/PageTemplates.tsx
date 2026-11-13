import { useId, type FormEventHandler, type ReactNode, type Ref } from "react";

type PageProps = {
  title: ReactNode;
  children?: ReactNode;
  navigation?: ReactNode;
  description?: ReactNode;
  eyebrow?: ReactNode;
  actions?: ReactNode;
  className?: string;
  headingId?: string;
};

type DetailProps = PageProps & {
  back?: ReactNode;
  status?: ReactNode;
  headingRef?: Ref<HTMLHeadingElement>;
};

const classes = (base: string, extra?: string) => extra ? `${base} ${extra}` : base;

/** A register's filters and toolbar are slots; the caller owns fetching and states. */
export function RegisterPageTemplate({
  title, children, navigation, description, eyebrow, actions, className, headingId,
  filters, filtersClassName, toolbar, footer,
}: PageProps & {
  filters?: ReactNode;
  filtersClassName?: string;
  toolbar?: ReactNode;
  footer?: ReactNode;
}) {
  const generatedId = useId();
  const titleId = headingId ?? generatedId;

  return <section className={classes("master-list", className)} aria-labelledby={titleId}>
    {navigation}
    <div className="page-heading-row">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h2 id={titleId}>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {actions}
    </div>
    {filters && <div className={classes("master-filters", filtersClassName)}>{filters}</div>}
    {toolbar && <div className="master-list__toolbar">{toolbar}</div>}
    {children}
    {footer}
  </section>;
}

/** A record page with a stable heading target for focus after navigation. */
export function DetailPageTemplate({
  title, children, navigation, description, eyebrow, actions, className, headingId,
  back, status, headingRef,
}: DetailProps) {
  const generatedId = useId();
  const titleId = headingId ?? generatedId;

  return <section className={classes("master-detail", className)} aria-labelledby={titleId}>
    {navigation}
    <div className="master-detail__heading">
      <div>
        {back}
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h2 id={titleId} ref={headingRef} tabIndex={headingRef ? -1 : undefined}>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      <div>{status}{actions}</div>
    </div>
    {children}
  </section>;
}

/** The caller supplies fields, validation, error messages, and submit behavior. */
export function FormPageTemplate({
  formTitle, onSubmit, formId, formActions, feedback, children, ...page
}: Omit<DetailProps, "children"> & {
  formTitle: ReactNode;
  onSubmit: FormEventHandler<HTMLFormElement>;
  formId?: string;
  formActions?: ReactNode;
  feedback?: ReactNode;
  children: ReactNode;
}) {
  return <DetailPageTemplate {...page}>
    <div className="form-panel">
      <form id={formId} className="record-form" onSubmit={onSubmit}>
        <div className="record-form__heading"><h3>{formTitle}</h3></div>
        {children}
        {feedback}
        {formActions && <div className="record-form__actions">{formActions}</div>}
      </form>
    </div>
  </DetailPageTemplate>;
}

function TitledPanel({ title, children, className }: {
  title: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const titleId = useId();
  return <section className={classes("record-panel", className)} aria-labelledby={titleId}>
    <div className="record-panel__heading"><h3 id={titleId}>{title}</h3></div>
    {children}
  </section>;
}

/** Supply the comparison table/cards as content; this adds no ranking logic. */
export function ComparisonPageTemplate({
  comparisonTitle, comparison, comparisonClassName, children, ...page
}: Omit<DetailProps, "children"> & {
  comparisonTitle: ReactNode;
  comparison: ReactNode;
  comparisonClassName?: string;
  children?: ReactNode;
}) {
  return <DetailPageTemplate {...page}>
    {children}
    <TitledPanel title={comparisonTitle} className={comparisonClassName}>{comparison}</TitledPanel>
  </DetailPageTemplate>;
}

/** Supply permitted decisions and history from the page's verified contract. */
export function ApprovalPageTemplate({
  workflowTitle, workflow, children, ...page
}: Omit<DetailProps, "children"> & {
  workflowTitle: ReactNode;
  workflow: ReactNode;
  children?: ReactNode;
}) {
  return <DetailPageTemplate {...page}>
    {children}
    <TitledPanel title={workflowTitle}>{workflow}</TitledPanel>
  </DetailPageTemplate>;
}
