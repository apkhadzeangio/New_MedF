$(document).ready(function () {

    $('input[id="doe"]').datepicker({
        dateFormat: "dd.mm.yy",
        // onSelect: function (selected_date, dp) {
        // }
    });
    $('input[id="dob"]').datepicker({
        dateFormat: "dd.mm.yy",
        changeYear: true,
        changeMonth: true,
        minDate: "-110y",
        // onSelect: function (selected_date, dp) {
        // }
    });
    $(".fdate1").datepicker({
        dateFormat: "dd.mm.yy",
        onSelect: function (selected_date, dp) {
            $('input[id="form-day1"]').val(selected_date);
            var date = $(this).datepicker('getDate');
            date.setDate(date.getDate() + 1);
            $('.fdate2').datepicker("setDate", date);
            $('.fdate2').datepicker("option", "dateFormat", 'dd.mm.yy');
            date.setDate(date.getDate() + 1);
            $('.fdate3').datepicker("setDate", date);
            $('.fdate3').datepicker("option", "dateFormat", 'dd.mm.yy');
            $('input[id="form-day2"]').val($('.fdate2').val());
            $('input[id="form-day3"]').val($('.fdate3').val());
        }
    });
    $( ".fdate2" ).datepicker({
        dateFormat: "dd.mm.yy",
        onSelect: function (selected_date, dp) {
            $('input[id="form-day2"]').val(selected_date);
            var date = $(this).datepicker('getDate');
            date.setDate(date.getDate() - 1);
            $('.fdate1').datepicker("setDate", date);
            $('.fdate1').datepicker("option", "dateFormat", 'dd.mm.yy');
            date.setDate(date.getDate() + 2);
            $('.fdate3').datepicker("setDate", date);
            $('.fdate3').datepicker("option", "dateFormat", 'dd.mm.yy');
            $('input[id="form-day1"]').val($('.fdate1').val());
            $('input[id="form-day3"]').val($('.fdate3').val());
        }
    });
    $( ".fdate3" ).datepicker({
        dateFormat: "dd.mm.yy",
        onSelect: function (selected_date, dp) {
            $('input[id="form-day3"]').val(selected_date);
            var date = $(this).datepicker('getDate');
            date.setDate(date.getDate() - 1);
            $('.fdate2').datepicker("setDate", date);
            $('.fdate2').datepicker("option", "dateFormat", 'dd.mm.yy');
            date.setDate(date.getDate() - 1);
            $('.fdate1').datepicker("setDate", date);
            $('.fdate1').datepicker("option", "dateFormat", 'dd.mm.yy');
            $('input[id="form-day1"]').val($('.fdate1').val());
            $('input[id="form-day2"]').val($('.fdate2').val());
        }
    });

    $('#create-template').on('click', function(e) {
        $('#t-form').submit();
    });

    $('td[class="hour"]').on('click', function (e) {
        var value = $(this).text();
        if (value !== '+') {
            $(this).text('+'); // — ○ ✔ html code &#10004
            var form = $('#niv-cover-template');
            if (form.length) {
                var field = this.id.split('-');
                form.append(`<input type='hidden' id='form-${this.id}' name='${field[0]}-${field[1]}' value='${field[2]}'/>`)
            }
        } else {
            $(this).text('');
            var t = $('input[name="t"]').length;
            var el = $(`input[id="form-${this.id}"]`);
            if (t & el.length) {
                el.val(`del-${el.val()}`);
            } else {
                el.remove();
            }
        }
    });

    $('.n-input-url').on('keyup', function (e) {
        $(`input[id="form-${this.id}"]`).val(this.value);
    });

    $('.m-input-url').on('keyup', function (e) {
        var inpId = this.id;
        $(`input[id="form-${inpId}"]`).val(this.value);
        var ddElem = $(`#div-${this.id}`);
        if (!this.value.trim()) {
            if (ddElem.length) {
                ddElem.removeClass('show');
                ddElem.empty();
            }

            var nId = inpId.replace('m', 'n');
            $(`input[id="${nId}"]`).val('');
            $(`input[id="form-${nId}"]`).val('');

            return
        }
        if (this.classList.value == 'n-input-url') {
            url = '/api/notes/?q=';
        } else {
            url = '/api/medicines/?q=';
        }
        if (ddElem.length) {
            $.get(`${url}${this.value}`, function (result) {
                ddElem.empty();
                if (result) {
                    var hrElem =  document.createElement('hr');
                    hrElem.setAttribute('class', 'mt-1 mb-1');
                    hrElem.setAttribute('style', 'margin-top: 15px;');
                    ddElem.append(hrElem);
                    result.forEach(function (i) {
                        var item = document.createElement('li');
                        item.setAttribute('class', 'dropdown-item its');
                        item.setAttribute('style', 'text-wrap: wrap; text-align: left; padding: 0; height: auto; min-height: 20px; margin-left: 5px; padding-top: 1px; line-height: 1.1;');
                        // item.setAttribute('href', '#');
                        item.setAttribute('data-inid', inpId);
                        item.textContent = `${i[0]} - ${i[1]}`;
                        ddElem.append(item);
                        var hrElem =  document.createElement('hr');
                        hrElem.setAttribute('class', 'mt-1 mb-1');
                        hrElem.setAttribute('style', 'margin-top: 15px;');
                        ddElem.append(hrElem);
                    });
                    ddElem.addClass('show');
                }
            });
            $(this).after(ddElem);
        } else {
            var ddElem = document.createElement('ul');
            ddElem.setAttribute('id', `div-${this.id}`);
            ddElem.setAttribute('class', 'dropdown-menu');
            ddElem.setAttribute('tabindex', '1');
            ddElem.setAttribute('style', 'width:95%; left: auto; max-height: 180px; height: auto; overflow-x: hidden; padding:0; margin:0;');
            $.get(`${url}${this.value}`, function (result) {
                if (result) {
                    ddElem.setAttribute('class', 'show');
                    var hrElem =  document.createElement('hr');
                    hrElem.setAttribute('class', 'mt-1 mb-1');
                    hrElem.setAttribute('style', 'margin-top: 15px;');
                    ddElem.append(hrElem);
                    result.forEach(function (i) {
                        var item = document.createElement('li');
                        item.setAttribute('class', 'dropdown-item its');
                        item.setAttribute('style', 'text-wrap: wrap; text-align: left; padding: 0; height: auto; min-height: 20px; margin-left: 5px; padding-top: 1px; line-height: 1.1;');
                        // item.setAttribute('href', '#');
                        item.setAttribute('data-inid', inpId);
                        item.textContent = `${i[0]} - ${i[1]}`;
                        ddElem.append(item);
                        var hrElem =  document.createElement('hr');
                        hrElem.setAttribute('class', 'mt-1 mb-1');
                        hrElem.setAttribute('style', 'margin-top: 15px;');
                        ddElem.append(hrElem);
                    })
                }
            });
            $(this).after(ddElem);
        }
    // for create template by form
    // $(`div[id="d${this.id}"]`).css('display', 'flex');
    });

    $('body').on('mousedown', '.its', function (e) {
        var [mVal, nVal] = this.textContent.split(' - ');
        $(`input[id="${this.dataset.inid}"]`).val(mVal);
        $(`input[id="form-${this.dataset.inid}"]`).val(mVal);
        // for notes
        var nId = this.dataset.inid.replace('m', 'n');
        if (nVal !== 'null') {
            $(`input[id="${nId}"]`).val(nVal);
            $(`input[id="form-${nId}"]`).val(nVal);
        }

        var ddElem = $(`#div-${this.dataset.inid}`);
        ddElem.removeClass('show');
        ddElem.empty();
    });

    $('input[id="doe"]').on('keyup change', function (e) {
        $(`input[id="form-doe"]`).val(this.value);
    });
    $('input[id="dob"]').on('keyup change', function (e) {
        $(`input[id="form-dob"]`).val(this.value);
    });

    $('input[id="patient_name"]').on('keyup change', function (e) {
        $(`input[id="form-patient_name"]`).val(this.value);
    });

    $('input[id="person_id"]').on('keyup change', function (e) {
        $(`input[id="form-person_id"]`).val(this.value);
    });

    $('input[id="mews_score"]').on('keyup change', function (e) {
        $(`input[id="form-mews_score"]`).val(this.value);
    });

    $('input[id="bmi_score"]').on('keyup change', function (e) {
        $(`input[id="form-bmi_score"]`).val(this.value);
    });

    $('input[id="gcs_score"]').on('keyup change', function (e) {
        $(`input[id="form-gcs_score"]`).val(this.value);
    });

    $('input[id="risk_falling"]').on('keyup change', function (e) {
        $(`input[id="form-risk_falling"]`).val(this.value);
    });

    $('select[id="radial_pulse"]').on('change', function (e) {
        $(`input[id="form-radial_pulse"]`).val(this.value);
    });

    $('input[id="neuro"]').on('change', function (e) {
        if ($(this).is(':checked')) {
            $(`input[id="form-neuro"]`).val("on");
        } else {
            $(`input[id="form-neuro"]`).val("");
        }

    });

    $('input[id="diabetes"]').on('change', function (e) {
        if ($(this).is(':checked')) {
            $(`input[id="form-diabetes"]`).val("on");
        } else {
            $(`input[id="form-diabetes"]`).val("");
        }
    });

    $('input[id="allergy"]').on('change', function (e) {
        if ($(this).is(':checked')) {
            $(`input[id="form-allergy"]`).val("on");
        } else {
            $(`input[id="form-allergy"]`).val("");
        }
    });

    $('input[id="card_number"]').on('keyup change', function (e) {
        $(`input[id="form-card_number"]`).val(this.value);
    });

    $('input[id="department"]').on('keyup change', function (e) {
        $(`input[id="form-department"]`).val(this.value);
    });

    $('input[id="chamber_number"]').on('keyup change', function (e) {
        $(`input[id="form-chamber_number"]`).val(this.value);
    });

    $('input[id="doctor"]').on('keyup change', function (e) {
        $(`input[id="form-doctor"]`).val(this.value);
    });

    $('input[id="nurse"]').on('keyup change', function (e) {
        $(`input[id="form-nurse"]`).val(this.value);
    });

    $('input[id="diagnosis"]').on('keyup change', function (e) {
        $(`input[id="form-diagnosis"]`).val(this.value);
    });

    $('input[id="title"]').on('keyup', function (e) {
        $(`input[id="form-title"]`).val(this.value);
        $('#saveNot').hide();
    });

    $('input[id="titleNew"]').on('keyup', function (e) {
        $(`input[id="form-title"]`).val(this.value);
        $('#saveAndNewNot').hide();
    });

    $('input[id="titleAs"]').on('keyup', function (e) {
        $(`input[id="form-as-title"]`).val(this.value);
        $('#saveAsNot').hide();
    });

    $('#saveModalBtn').on('click', function (e) {
        var title = $(`input[id="title"]`).val();
        if (!title) {
            $('#saveNot').show();
        } else {
            $('#niv-cover-template').submit();
        }
    });

    $('#saveAndNewModalBtn').on('click', function (e) {
        $('input[name="action"]').val('save_and_new');
        var title = $(`input[id="titleNew"]`).val();
        if (!title) {
            $('#saveAndNewNot').show();
        } else {
            $('#niv-cover-template').submit();
        }
    });

    $('#saveAsModalBtn').on('click', function (e) {
        $('input[name="action"]').val('save_as');
        var title = $(`input[id="titleAs"]`).val();
        if (!title) {
            $('#saveAsNot').show();
        } else {
            $('#niv-cover-template').submit();
        }
    });

    $('#updateBtn').on('click', function (e) {
        $('input[name="action"]').val('save');
        $('#niv-cover-template').submit();
    });

    $('#updateAndNewBtn').on('click', function (e) {
        $('input[name="action"]').val('save_and_new');
        $('#niv-cover-template').submit();
    });

    $('#saveAsBtn').on('click', function (e) {
        $('input[name="action"]').val('save_as');
        $('#niv-cover-template').submit();
    });

    $('a#niv-del-btn').on('click', function (e) {
        $.ajax({
            url: this.dataset.href,
            type: 'DELETE',
            success: function(result) {}
        });
        window.location.reload(true);
    });

    $('.copy-btn').on('click', function (e) {
        $.notify("Copied", "success", {position:"top left"});
    });
    new ClipboardJS('.copy-btn');

    $('input[name="q"]').on('keyup', function (e) {
        let sForm = $('#search-form');
        let csrfToken = $('input[name="csrfmiddlewaretoken"]').val();
        // if (this.value.length >= 2) {
       $.ajax({
            url: sForm.attr('action'),
            method: 'POST',
            data: {q: this.value, csrfmiddlewaretoken: csrfToken},
            success: function(result) {
                let nivForms = $('#niv-forms');
                nivForms.empty();
                nivForms.append(result);
            }
        });
        // }
    });

});
