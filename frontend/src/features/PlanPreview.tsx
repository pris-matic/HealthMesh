import type { PlanDto } from '../types';

export function PlanPreview({ plan }: { plan: PlanDto }) {
  const time = new Intl.DateTimeFormat('en-PH', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Manila' }).format(new Date(plan.as_of));
  return <section aria-labelledby="plan-title">
    <div className="section-heading"><h2 id="plan-title">Conditional alternatives</h2><span>Frozen revision {plan.revision}</span></div>
    <p>Simulated time: {time} · Asia/Manila (UTC+08:00)</p>
    <p>Each option proposes the full 40-box net request. Mandatory evidence must be verified before human review. No transfer is dispatched.</p>
    {plan.infeasible_reasons.length > 0 && <ul>{plan.infeasible_reasons.map((reason, i) => <li key={i}>{reason}</li>)}</ul>}
    <div className="branches">{plan.branches.map((branch, index) => <article className="panel" key={branch.id}>
      <span className="badge">{branch.status === 'PROVISIONAL' ? 'Provisional' : branch.status === 'READY_FOR_REVIEW' ? 'Ready for review' : 'Infeasible'}</span>
      <h3>Option {index + 1} · {branch.allocations.map(a => a.facility_name).join(' + ')}</h3>
      <ul className="allocations">{branch.allocations.map(a => <li key={a.batch_id}><strong>{a.quantity} {a.quantity === 1 ? a.base_unit : a.base_unit === 'box' ? 'boxes' : a.base_unit}</strong> from {a.facility_name}<br/><small>Verified transferable stock: {a.transferable === null ? 'Unknown' : `${a.transferable} ${a.base_unit === 'box' ? 'boxes' : a.base_unit}`}</small></li>)}</ul>
      <h4>Conditions to resolve</h4>
      <ul>{branch.conditions.map((condition, i) => <li key={i}>{condition}</li>)}</ul>
      <details><summary>Constraint checks and reasons</summary><ul>{branch.checks.map((check, i) => <li key={i}><strong>{check.status}</strong> · {check.field}: {check.reason}</li>)}</ul></details>
      {branch.rejected_reasons.length > 0 && <ul>{branch.rejected_reasons.map((reason, i) => <li key={i}>{reason}</li>)}</ul>}
      <p className="muted">Review is unavailable in this preview.</p><button disabled>Review option {index + 1}</button>
    </article>)}</div>
    <section className="panel"><h3>Mandatory unverified facts</h3><ul>{plan.mandatory_unverified_facts.map((fact, i) => <li key={i}>{fact.field}: {fact.reason}</li>)}</ul></section>
  </section>;
}
