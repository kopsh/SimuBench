import * as Primitive from '@radix-ui/react-primitive';
import * as RovingFocusGroup from '@radix-ui/react-roving-focus';
import * as React from 'react';

declare const InputValidationType: {
    readonly Alpha: "alpha";
    readonly Numeric: "numeric";
    readonly Alphanumeric: "alphanumeric";
    readonly None: "none";
};
type InputValidationType = (typeof InputValidationType)[keyof typeof InputValidationType];
declare const InputType: {
    readonly Password: "password";
    readonly Text: "text";
};
type InputType = (typeof InputType)[keyof typeof InputType];
declare const AutoComplete: {
    readonly Off: "off";
    readonly OneTimeCode: "one-time-code";
};
type AutoComplete = (typeof AutoComplete)[keyof typeof AutoComplete];
type RovingFocusGroupProps = RovingFocusGroup.RovingFocusGroupProps;
interface OneTimePasswordFieldOwnProps {
    /**
     * Specifies what—if any—permission the user agent has to provide automated
     * assistance in filling out form field values, as well as guidance to the
     * browser as to the type of information expected in the field. Allows
     * `"one-time-code"` or `"off"`.
     *
     * @defaultValue `"one-time-code"`
     * @see https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Attributes/autocomplete
     */
    autoComplete?: AutoComplete | undefined;
    /**
     * Whether or not the first fillable input should be focused on page-load.
     *
     * @defaultValue `false`
     * @see https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Global_attributes/autofocus
     */
    autoFocus?: boolean | undefined;
    /**
     * Whether or not the component should attempt to automatically submit when
     * all fields are filled. If the field is associated with an HTML `form`
     * element, the form's `requestSubmit` method will be called.
     *
     * @defaultValue `false`
     */
    autoSubmit?: boolean | undefined;
    /**
     * The initial value of the uncontrolled field.
     */
    defaultValue?: string | undefined;
    /**
     * Indicates the horizontal directionality of the parent element's text.
     *
     * @see https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Global_attributes/dir
     */
    dir?: RovingFocusGroupProps['dir'] | undefined;
    /**
     * Whether or not the field's input elements are disabled.
     */
    disabled?: boolean | undefined;
    /**
     * A string specifying the `form` element with which the input is associated.
     * This string's value, if present, must match the id of a `form` element in
     * the same document.
     *
     * @see https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/input#form
     */
    form?: string | undefined;
    /**
     * A string specifying a name for the input control. This name is submitted
     * along with the control's value when the form data is submitted.
     *
     * @see https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/input#name
     */
    name?: string | undefined;
    /**
     * When the `autoSubmit` prop is set to `true`, this callback will be fired
     * before attempting to submit the associated form. It will be called whether
     * or not a form is located, or if submission is not allowed.
     */
    onAutoSubmit?: {
        bivarianceHack(value: string): void;
    }['bivarianceHack'] | undefined;
    /**
     * A callback fired when the field's value changes. When the component is
     * controlled, this should update the state passed to the `value` prop.
     */
    onValueChange?: {
        bivarianceHack(value: string): void;
    }['bivarianceHack'] | undefined;
    /**
     * Indicates the vertical directionality of the input elements.
     *
     * @defaultValue `"horizontal"`
     */
    orientation?: RovingFocusGroupProps['orientation'] | undefined;
    /**
     * Defines the text displayed in a form control when the control has no value.
     *
     * @see https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Attributes/placeholder
     */
    placeholder?: string | undefined;
    /**
     * Whether or not the input elements can be updated by the user.
     *
     * @see https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Attributes/readonly
     */
    readOnly?: boolean | undefined;
    /**
     * Function for custom sanitization when `validationType` is set to `"none"`.
     * This function will be called before updating values in response to user
     * interactions.
     */
    sanitizeValue?: {
        bivarianceHack(value: string): string;
    }['bivarianceHack'] | undefined;
    /**
     * The input type of the field's input elements. Can be `"password"` or `"text"`.
     */
    type?: InputType | undefined;
    /**
     * Specifies the type of input validation to be used. Can be `"alpha"`,
     * `"numeric"`, `"alphanumeric"` or `"none"`.
     *
     * @defaultValue `"numeric"`
     */
    validationType?: InputValidationType | undefined;
    /**
     * The controlled value of the field.
     */
    value?: string | undefined;
}
interface OneTimePasswordFieldProps extends OneTimePasswordFieldOwnProps, Omit<Primitive.PrimitivePropsWithRef<'div'>, keyof OneTimePasswordFieldOwnProps> {
}
declare const OneTimePasswordField: React.ForwardRefExoticComponent<Omit<OneTimePasswordFieldProps, "ref"> & React.RefAttributes<HTMLDivElement>>;
interface OneTimePasswordFieldHiddenInputProps extends Omit<React.ComponentProps<'input'>, keyof 'value' | 'defaultValue' | 'type' | 'onChange' | 'readOnly' | 'disabled' | 'autoComplete' | 'autoFocus'> {
}
declare const OneTimePasswordFieldHiddenInput: React.ForwardRefExoticComponent<Omit<OneTimePasswordFieldHiddenInputProps, "ref"> & React.RefAttributes<HTMLInputElement>>;
interface OneTimePasswordFieldInputProps extends Omit<Primitive.PrimitivePropsWithRef<'input'>, 'value' | 'defaultValue' | 'disabled' | 'readOnly' | 'autoComplete' | 'autoFocus' | 'form' | 'name' | 'placeholder' | 'type'> {
    /**
     * Callback fired when the user input fails native HTML input validation.
     */
    onInvalidChange?: {
        bivarianceHack(character: string): void;
    }['bivarianceHack'] | undefined;
    /**
     * User-provided index to determine the order of the inputs. This is useful if
     * you need certain index-based attributes to be set on the initial render,
     * often to prevent flickering after hydration.
     */
    index?: number | undefined;
}
declare const OneTimePasswordFieldInput: React.ForwardRefExoticComponent<Omit<OneTimePasswordFieldInputProps, "ref"> & React.RefAttributes<HTMLInputElement>>;

export { OneTimePasswordFieldHiddenInput as HiddenInput, OneTimePasswordFieldInput as Input, InputValidationType, OneTimePasswordField, OneTimePasswordFieldHiddenInput, type OneTimePasswordFieldHiddenInputProps, OneTimePasswordFieldInput, type OneTimePasswordFieldInputProps, type OneTimePasswordFieldProps, OneTimePasswordField as Root };
