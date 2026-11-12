import * as _radix_ui_react_context from '@radix-ui/react-context';
import { Scope } from '@radix-ui/react-context';
import * as React from 'react';
import { Label } from '@radix-ui/react-label';
import { Primitive } from '@radix-ui/react-primitive';

type ScopedProps<P> = P & {
    __scopeForm?: Scope | undefined;
};
declare const createFormScope: _radix_ui_react_context.CreateScope;
type PrimitiveFormProps = React.ComponentPropsWithoutRef<typeof Primitive.form>;
interface FormProps extends PrimitiveFormProps {
    onClearServerErrors?: (() => void) | undefined;
}
declare const Form: React.ForwardRefExoticComponent<FormProps & React.RefAttributes<HTMLFormElement>>;
type PrimitiveDivProps = React.ComponentPropsWithoutRef<typeof Primitive.div>;
interface FormFieldProps extends PrimitiveDivProps {
    name: string;
    serverInvalid?: boolean | undefined;
}
declare const FormField: React.ForwardRefExoticComponent<FormFieldProps & React.RefAttributes<HTMLDivElement>>;
type LabelProps = React.ComponentPropsWithoutRef<typeof Label>;
interface FormLabelProps extends LabelProps {
    name?: string | undefined;
}
declare const FormLabel: React.ForwardRefExoticComponent<FormLabelProps & React.RefAttributes<HTMLLabelElement>>;
type PrimitiveInputProps = React.ComponentPropsWithoutRef<typeof Primitive.input>;
interface FormControlProps extends PrimitiveInputProps {
}
declare const FormControl: React.ForwardRefExoticComponent<FormControlProps & React.RefAttributes<HTMLInputElement>>;
declare const _validityMatchers: readonly ["badInput", "patternMismatch", "rangeOverflow", "rangeUnderflow", "stepMismatch", "tooLong", "tooShort", "typeMismatch", "valid", "valueMissing"];
type ValidityMatcher = (typeof _validityMatchers)[number];
interface FormMessageProps extends Omit<FormMessageImplProps, 'name'> {
    match?: ValidityMatcher | CustomMatcher | undefined;
    forceMatch?: boolean | undefined;
    name?: string | undefined;
}
declare const FormMessage: React.ForwardRefExoticComponent<FormMessageProps & React.RefAttributes<HTMLSpanElement>>;
type PrimitiveSpanProps = React.ComponentPropsWithoutRef<typeof Primitive.span>;
interface FormMessageImplProps extends PrimitiveSpanProps {
    name: string;
}
interface FormValidityStateProps {
    children(validity: ValidityState | undefined): React.ReactNode;
    name?: string | undefined;
}
declare const FormValidityState: (props: ScopedProps<FormValidityStateProps>) => React.JSX.Element;
type PrimitiveButtonProps = React.ComponentPropsWithoutRef<typeof Primitive.button>;
interface FormSubmitProps extends PrimitiveButtonProps {
}
declare const FormSubmit: React.ForwardRefExoticComponent<FormSubmitProps & React.RefAttributes<HTMLButtonElement>>;
type SyncCustomMatcher = (value: string, formData: FormData) => boolean;
type AsyncCustomMatcher = (value: string, formData: FormData) => Promise<boolean>;
type CustomMatcher = SyncCustomMatcher | AsyncCustomMatcher;

export { FormControl as Control, FormField as Field, Form, FormControl, type FormControlProps, FormField, type FormFieldProps, FormLabel, type FormLabelProps, FormMessage, type FormMessageProps, type FormProps, FormSubmit, type FormSubmitProps, FormValidityState, type FormValidityStateProps, FormLabel as Label, FormMessage as Message, Form as Root, FormSubmit as Submit, FormValidityState as ValidityState, createFormScope };
