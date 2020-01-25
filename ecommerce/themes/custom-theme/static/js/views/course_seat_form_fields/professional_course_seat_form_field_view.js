define([
    'underscore',
    'views/course_seat_form_fields/verified_course_seat_form_field_view',
    'text!templates/professional_course_seat_form_field.html'
],
    function(_,
             VerifiedCourseSeatFormFieldView,
             FieldTemplate) {
        'use strict';

        return VerifiedCourseSeatFormFieldView.extend({
            certificateType: 'professional',
            idVerificationRequired: false,
            seatType: 'professional',
	    bindings: {
                'input[name=certificate_type]': 'certificate_type',
                'input[name=price]': {
                    observe: 'price',
                    setOptions: {
                        validate: true
                    }
                },
		'input[name=inr_price]': {
                    observe: 'inr_price',
                    setOptions: {
                        validate: false
                    }
                },
                'input[name=expires]': {
                    observe: 'expires',
                    setOptions: {
                        validate: true
                    }
                },
                'input[name=id_verification_required]': {
                    observe: 'id_verification_required',
                    onSet: 'cleanIdVerificationRequired'
                }
            },
            template: _.template(FieldTemplate)
        });
    }
);
