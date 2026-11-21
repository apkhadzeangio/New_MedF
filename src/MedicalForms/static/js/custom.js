/*
 * Medical Forms - page behaviour.
 *
 * The cover page (niv.html) keeps every value in a hidden form
 * (#niv-cover-template). Visible inputs are mirrored into hidden inputs with
 * the id "form-<visible id>", and that hidden form is what gets saved.
 */
$(document).ready(function () {

    var $saveForm = $('#niv-cover-template');
    var hasSaveForm = $saveForm.length > 0;
    var isDirty = false;
    var isSubmitting = false;

    function markDirty() {
        if (hasSaveForm) {
            isDirty = true;
        }
    }

    function getCookie(name) {
        var match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
        return match ? decodeURIComponent(match[1]) : null;
    }

    function csrfToken() {
        return getCookie('csrftoken') || $('input[name="csrfmiddlewaretoken"]').first().val();
    }

    function debounce(fn, wait) {
        var timer = null;
        return function () {
            var ctx = this, args = arguments;
            clearTimeout(timer);
            timer = setTimeout(function () { fn.apply(ctx, args); }, wait);
        };
    }

    function submitSaveForm() {
        if (isSubmitting) {
            return;
        }
        isSubmitting = true;
        isDirty = false;
        $('#updateBtn, #updateAndNewBtn, #saveModalBtn, #saveAndNewModalBtn, #saveAsModalBtn').prop('disabled', true);
        $saveForm.submit();
    }

    /* ------------------------------------------------------------------ */
    /* Dates                                                               */
    /* ------------------------------------------------------------------ */

    $('input[id="doe"]').datepicker({
        dateFormat: "dd.mm.yy",
    });
    $('input[id="dob"]').datepicker({
        dateFormat: "dd.mm.yy",
        changeYear: true,
        changeMonth: true,
        minDate: "-110y",
        yearRange: "-110:+0",
    });

    // The three day columns are always consecutive dates: choosing one fills the others.
    function setDays(changedIndex, selected) {
        var base = $('.fdate' + changedIndex).datepicker('getDate');
        if (!base) {
            return;
        }
        for (var i = 1; i <= 3; i++) {
            var $el = $('.fdate' + i);
            if (i !== changedIndex) {
                var date = new Date(base.getTime());
                date.setDate(date.getDate() + (i - changedIndex));
                $el.datepicker('setDate', date);
            }
            $('input[id="form-day' + i + '"]').val($el.val());
        }
        markDirty();
    }

    [1, 2, 3].forEach(function (i) {
        $('.fdate' + i).datepicker({
            dateFormat: "dd.mm.yy",
            onSelect: function (selected) { setDays(i, selected); }
        }).on('input', function () {
            // typed by hand
            $('input[id="form-day' + i + '"]').val(this.value);
            markDirty();
        });
    });

    $('#create-template').on('click', function () {
        $('#t-form').submit();
    });

    /* ------------------------------------------------------------------ */
    /* Hour cells                                                          */
    /* ------------------------------------------------------------------ */

    // Column letters per day block. Day 1 starts with an extra 08:00 column.
    var DAY_LETTERS = {
        d1: 'abcdefghijklm',   // 8 10 12 14 16 18 20 22 24 2 4 6 8
        d2: 'abcdefghijkl',    //   10 12 14 16 18 20 22 24 2 4 6 8
        d3: 'abcdefghijkl'
    };

    function setHour(cell, on) {
        var $cell = $(cell);
        var parts = cell.id.split('-');          // ["r1", "d1", "a"]
        var $hidden = $('input[id="form-' + cell.id + '"]');
        if (on) {
            $cell.text('+');
            if (hasSaveForm) {
                $hidden.remove();
                $saveForm.append(
                    $('<input type="hidden">')
                        .attr('id', 'form-' + cell.id)
                        .attr('name', parts[0] + '-' + parts[1])
                        .val(parts[2])
                );
            }
        } else {
            $cell.text('');
            var isExisting = $('input[name="t"]').length > 0;
            if (isExisting && $hidden.length) {
                // keep the key so the server knows this hour was removed
                $hidden.val('del-' + parts[2]);
            } else {
                $hidden.remove();
            }
        }
    }

    // Cells for the same clock hour in all three days.
    function sameHourCells(cell) {
        var parts = cell.id.split('-');
        var row = parts[0], day = parts[1], letter = parts[2];
        var index = DAY_LETTERS[day].indexOf(letter);
        // slot: 0 = 10:00 ... 11 = next morning 08:00; day 1 "a" (08:00) has no pair
        var slot = day === 'd1' ? index - 1 : index;
        if (slot < 0) {
            return [cell];
        }
        var ids = [
            row + '-d1-' + DAY_LETTERS.d1.charAt(slot + 1),
            row + '-d2-' + DAY_LETTERS.d2.charAt(slot),
            row + '-d3-' + DAY_LETTERS.d3.charAt(slot)
        ];
        return ids.map(function (id) { return document.getElementById(id); })
                  .filter(function (el) { return el; });
    }

    $('td.hour').on('click', function (e) {
        var turnOn = $(this).text().trim() !== '+';
        var cells = e.shiftKey ? sameHourCells(this) : [this];
        cells.forEach(function (cell) { setHour(cell, turnOn); });
        markDirty();
    }).on('mousedown', function (e) {
        if (e.shiftKey) {
            e.preventDefault();   // no text selection on shift-click
        }
    });

    /* ------------------------------------------------------------------ */
    /* Mirror visible inputs into the hidden save form                     */
    /* ------------------------------------------------------------------ */

    var TEXT_FIELDS = [
        'doe', 'dob', 'patient_name', 'person_id', 'mews_score', 'bmi_score',
        'gcs_score', 'risk_falling', 'card_number', 'department',
        'chamber_number', 'doctor', 'nurse', 'diagnosis'
    ];
    TEXT_FIELDS.forEach(function (name) {
        $('input[id="' + name + '"]').on('input change', function () {
            $('input[id="form-' + name + '"]').val(this.value);
            markDirty();
        });
    });

    $('select[id="radial_pulse"]').on('change', function () {
        $('input[id="form-radial_pulse"]').val(this.value);
        markDirty();
    });

    ['neuro', 'diabetes', 'allergy'].forEach(function (name) {
        $('input[id="' + name + '"]').on('change', function () {
            $('input[id="form-' + name + '"]').val($(this).is(':checked') ? 'on' : '');
            markDirty();
        });
    });

    $('.n-input-url').on('input change', function () {
        $('input[id="form-' + this.id + '"]').val(this.value);
        markDirty();
    });

    /* ------------------------------------------------------------------ */
    /* Medicine autocomplete                                               */
    /* ------------------------------------------------------------------ */

    var acRequest = null;

    function noteInputId(medInputId) {
        return medInputId.replace('m', 'n');
    }

    function getDropdown(input) {
        var $dd = $('#div-' + input.id);
        if (!$dd.length) {
            $dd = $('<ul class="dropdown-menu ac-menu" tabindex="-1"></ul>')
                .attr('id', 'div-' + input.id);
            $(input).after($dd);
        }
        return $dd;
    }

    function closeDropdown(input) {
        $('#div-' + input.id).removeClass('show').empty();
    }

    function chooseMedicine(inputId, name, note) {
        $('input[id="' + inputId + '"]').val(name);
        $('input[id="form-' + inputId + '"]').val(name);
        if (note !== null && note !== undefined && note !== '') {
            var nId = noteInputId(inputId);
            $('input[id="' + nId + '"]').val(note);
            $('input[id="form-' + nId + '"]').val(note);
        }
        closeDropdown({id: inputId});
        markDirty();
    }

    var searchMedicines = debounce(function (input) {
        var q = input.value.trim();
        if (!q) {
            return;
        }
        if (acRequest) {
            acRequest.abort();
        }
        acRequest = $.get('/api/medicines/', {q: q}, function (result) {
            if (input.value.trim() !== q) {
                return;  // user kept typing
            }
            var $dd = getDropdown(input);
            $dd.empty();
            if (!result || !result.length) {
                $dd.removeClass('show');
                return;
            }
            result.forEach(function (item) {
                var name = item[0], note = item[1];
                var $li = $('<li class="dropdown-item its"></li>')
                    .attr('data-inid', input.id)
                    .attr('data-name', name)
                    .attr('data-note', note === null ? '' : note);
                $li.append($('<span class="ac-name"></span>').text(name));
                if (note) {
                    $li.append(' ').append($('<span class="ac-note"></span>').text('- ' + note));
                }
                $dd.append($li);
            });
            $dd.addClass('show');
        });
    }, 150);

    $('.m-input-url').on('input', function () {
        $('input[id="form-' + this.id + '"]').val(this.value);
        markDirty();
        if (!this.value.trim()) {
            closeDropdown(this);
            var nId = noteInputId(this.id);
            $('input[id="' + nId + '"]').val('');
            $('input[id="form-' + nId + '"]').val('');
            return;
        }
        searchMedicines(this);
    }).on('keydown', function (e) {
        var $dd = $('#div-' + this.id);
        if (!$dd.hasClass('show')) {
            return;
        }
        var $items = $dd.find('.its');
        var $active = $items.filter('.active');
        var idx = $items.index($active);
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            idx = e.key === 'ArrowDown' ? Math.min(idx + 1, $items.length - 1) : Math.max(idx - 1, 0);
            $items.removeClass('active');
            var el = $items.eq(idx).addClass('active').get(0);
            if (el) {
                el.scrollIntoView({block: 'nearest'});
            }
        } else if (e.key === 'Enter') {
            if ($active.length) {
                e.preventDefault();
                chooseMedicine(this.id, $active.attr('data-name'), $active.attr('data-note'));
            }
        } else if (e.key === 'Escape') {
            closeDropdown(this);
        }
    }).on('blur', function () {
        var input = this;
        setTimeout(function () { closeDropdown(input); }, 150);
    });

    $('body').on('mousedown', '.its', function (e) {
        e.preventDefault();
        var $li = $(this);
        chooseMedicine($li.attr('data-inid'), $li.attr('data-name'), $li.attr('data-note'));
    });

    /* ------------------------------------------------------------------ */
    /* Save buttons and dialogs                                            */
    /* ------------------------------------------------------------------ */

    $('input[id="title"]').on('input', function () {
        $('input[id="form-title"]').val(this.value);
        $('#saveNot').hide();
    });

    $('input[id="titleNew"]').on('input', function () {
        $('input[id="form-title"]').val(this.value);
        $('#saveAndNewNot').hide();
    });

    $('input[id="titleAs"]').on('input', function () {
        $('input[id="form-as-title"]').val(this.value);
        $('#saveAsNot').hide();
    });

    $('#saveModalBtn').on('click', function () {
        if (!$('input[id="title"]').val().trim()) {
            $('#saveNot').show();
        } else {
            submitSaveForm();
        }
    });

    $('#saveAndNewModalBtn').on('click', function () {
        $('input[name="action"]').val('save_and_new');
        if (!$('input[id="titleNew"]').val().trim()) {
            $('#saveAndNewNot').show();
        } else {
            submitSaveForm();
        }
    });

    $('#saveAsModalBtn').on('click', function () {
        $('input[name="action"]').val('save_as');
        if (!$('input[id="titleAs"]').val().trim()) {
            $('#saveAsNot').show();
        } else {
            submitSaveForm();
        }
    });

    $('#updateBtn').on('click', function () {
        $('input[name="action"]').val('save');
        submitSaveForm();
    });

    $('#updateAndNewBtn').on('click', function () {
        $('input[name="action"]').val('save_and_new');
        submitSaveForm();
    });

    $('#saveAsBtn').on('click', function () {
        $('input[name="action"]').val('save_as');
        submitSaveForm();
    });

    // Focus the name field when a dialog opens; Enter = save.
    $('.modal').on('shown.bs.modal', function () {
        $(this).find('input[type="text"]').first().trigger('focus');
    });
    $('#title, #titleNew, #titleAs').on('keydown', function (e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            $(this).closest('.modal').find('.modal-footer .btn-primary').trigger('click');
        }
    });

    // Ctrl+S / Cmd+S saves the template.
    $(document).on('keydown', function (e) {
        if (!hasSaveForm || !(e.ctrlKey || e.metaKey) || (e.key !== 's' && e.key !== 'S' && e.key !== 'ს')) {
            return;
        }
        e.preventDefault();
        if ($('.modal.show').length) {
            $('.modal.show .modal-footer .btn-primary').trigger('click');
        } else if ($('#updateBtn').length) {
            $('#updateBtn').trigger('click');
        } else if ($('#saveModal').length && window.bootstrap) {
            bootstrap.Modal.getOrCreateInstance(document.getElementById('saveModal')).show();
        }
    });

    // Warn before leaving the page with unsaved changes.
    $(window).on('beforeunload', function (e) {
        if (isDirty && !isSubmitting) {
            e.preventDefault();
            e.returnValue = '';
            return '';
        }
    });

    // Printing shows the last *saved* version - warn if there are unsaved edits.
    $('a[href*="/print/"]').on('click', function (e) {
        if (isDirty && !confirm('ცვლილებები ჯერ არ არის შენახული.\nდაიბეჭდება ბოლოს შენახული ვერსია.\n\nგავაგრძელო ბეჭდვა?')) {
            e.preventDefault();
        }
    });

    /* ------------------------------------------------------------------ */
    /* Templates list: delete, copy, live search                           */
    /* ------------------------------------------------------------------ */

    $(document).on('click', '.niv-del-btn', function (e) {
        e.preventDefault();
        var title = this.dataset.title || '';
        if (!confirm('ნამდვილად გსურთ შაბლონის წაშლა?\n\n' + title)) {
            return;
        }
        $.ajax({
            url: this.dataset.href,
            type: 'POST',
            headers: {'X-CSRFToken': csrfToken()},
            success: function () {
                window.location.reload();
            },
            error: function () {
                alert('წაშლა ვერ მოხერხდა. განაახლეთ გვერდი და სცადეთ თავიდან.');
            }
        });
    });

    if (window.ClipboardJS) {
        var clipboard = new ClipboardJS('.copy-btn');
        clipboard.on('success', function () {
            $.notify('დაკოპირდა', 'success');
        });
    }

    var searchRequest = null;
    var runSearch = debounce(function (value) {
        var $sForm = $('#search-form');
        if (searchRequest) {
            searchRequest.abort();
        }
        searchRequest = $.ajax({
            url: $sForm.attr('action'),
            method: 'POST',
            data: {q: value, csrfmiddlewaretoken: csrfToken()},
            success: function (result) {
                $('#niv-forms').html(result);
                try {
                    var url = new URL(window.location.href);
                    url.searchParams.delete('page');
                    if (value) {
                        url.searchParams.set('q', value);
                    } else {
                        url.searchParams.delete('q');
                    }
                    window.history.replaceState(null, '', url.toString());
                } catch (err) { /* old browser */ }
            }
        });
    }, 250);

    $('#search-form input[name="q"]').on('input', function () {
        runSearch(this.value.trim());
    });
    $('#search-form').on('submit', function (e) {
        e.preventDefault();
        runSearch($(this).find('input[name="q"]').val().trim());
    });

});
