"use client";

import { Children, isValidElement, type ReactNode, type SelectHTMLAttributes } from "react";
import { ChevronDown, HelpCircle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "./card";
import { Tooltip } from "./tooltip";

export function ScientificControlPanel({ title, help, action, status, children }: {
  title:string; help:string; action:ReactNode; status?:ReactNode; children:ReactNode;
}) {
  return <Card className="scientific-controls">
    <CardHeader className="scientific-controls__header">
      <div className="scientific-controls__toolbar">
        <CardTitle>{title}</CardTitle>
        <Tooltip label={help}><button type="button" className="scientific-controls__help" aria-label={help}><HelpCircle size={13}/></button></Tooltip>
        {action}
      </div>
      {status&&<div className="scientific-controls__status" role="status">{status}</div>}
    </CardHeader>
    <CardContent className="scientific-controls__body">{children}</CardContent>
  </Card>;
}

export function ScientificControlGroup({ title, children }: {title:string;children:ReactNode}) {
  return <section className="scientific-controls__group" aria-label={title}><h4>{title}</h4>{children}</section>;
}

/** Native selection and keyboard behavior, with a wrapping display for long values. */
export function ScientificSelect({children,className,...props}:SelectHTMLAttributes<HTMLSelectElement>) {
  const options=Children.toArray(children).filter(isValidElement<{value?:string|number;children?:ReactNode}>);
  const selected=options.find(option=>String(option.props.value??option.props.children)===String(props.value));
  const label=selected?.props.children??String(props.value??"");
  return <span className="scientific-select" data-disabled={props.disabled||undefined}>
    <span aria-hidden="true" className="scientific-select__value">{label}</span><ChevronDown size={13} aria-hidden="true"/>
    <select {...props} className={className} title={typeof label==="string"?label:props.title}>{children}</select>
  </span>;
}
