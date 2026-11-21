

class ModelFilterBackend:

    @classmethod
    def filter_queryset(cls, request, queryset, view=None):
        q = request.POST.get('q', request.GET.get('q'))
        return queryset.filter(name__icontains=str(q).strip())
